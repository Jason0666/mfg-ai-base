"""FastAPI 入口（§3.1）。

启动流程：
1. 加载 config
2. 平台表初始化（幂等）+ 种子用户 + manifest 同步 app_module
3. 扫描 modules/ 目录，注册模块
4. 挂载平台路由（/api/health, /api/registry/*, /api/files/*）
5. 挂载每个模块的路由（/api/modules/{code}）
6. CORS + 鉴权中间件（仅 DEMO_MODE=false 生效）+ 限流

DoD：docker compose up 后访问首页能看到模块卡片。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.api import health as health_api
from app.api import registry as registry_api
from app.api import files as files_api
from app.api import auth as auth_api
from app.api import admin as admin_api
from app.core import audit
from app.core.registry import get_registry, mount_module_routers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """启动事件：平台表 → 种子用户 → 扫描模块 → 同步 app_module → 挂载路由。"""
    logger.info("=== mfg-ai-base backend starting ===")
    logger.info("DEMO_MODE=%s APP_ENV=%s", settings.DEMO_MODE, settings.APP_ENV)
    # 平台四表（任何模式都需要：审计持久化不依赖 DEMO）
    try:
        from app.core.db import bootstrap_users, ensure_platform_tables, sync_modules

        ensure_platform_tables()
        bootstrap_users()
    except Exception as e:  # noqa: BLE001
        logger.exception("platform tables init failed (non-fatal): %s", e)
    # DEMO 模式：初始化业务种子数据（biz_prod_metric 等，幂等）
    if settings.DEMO_MODE:
        try:
            from seed.load_seed import init_seed

            init_seed()
        except Exception as e:  # noqa: BLE001
            logger.exception("seed init failed (non-fatal): %s", e)
    modules_dir = Path(settings.MODULES_DIR).resolve()
    logger.info("scanning modules at %s", modules_dir)
    get_registry().scan(modules_dir)
    # 模块数
    modules = get_registry().get_all()
    logger.info("loaded %d modules: %s", len(modules), [m.code for m in modules])
    # manifest 同步进 app_module（模块级权限的数据基础）
    try:
        from app.core.db import sync_modules

        sync_modules([m.model_dump(exclude={"module_dir"}) for m in modules])
    except Exception as e:  # noqa: BLE001
        logger.exception("sync app_module failed (non-fatal): %s", e)
    # 挂载模块路由（/api/modules/{code}）
    mount_module_routers(app)
    logger.info("=== startup done ===")
    yield
    logger.info("=== backend shutting down ===")


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------
# 限流：per-IP 滑动窗口（简单内存实现，单进程交付形态够用）
_rate_buckets: dict[str, deque] = defaultdict(deque)

# 免鉴权路径（PROD 模式）
_PUBLIC_EXACT = {"/", "/openapi.json", "/favicon.ico"}
_PUBLIC_PREFIX = ("/docs", "/api/health", "/api/auth/login", "/api/registry")


def _is_public(path: str, method: str) -> bool:
    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIX):
        return True
    # 只读接口放行：模块 GET（sample 等只读 extra endpoint）、文件下载
    if method == "GET" and (
        path.startswith("/api/modules/") or path.startswith("/api/files/")
    ):
        return True
    return False


async def _dispatch_common(request: Request, call_next):  # type: ignore[no-untyped-def]
    """鉴权 + 限流的统一处理体。"""
    path = request.url.path
    method = request.method

    # CORS 预检直接放行
    if method == "OPTIONS":
        return await call_next(request)

    # ---- 限流（§12.4）：除健康检查外全部计数 ----
    if path != "/api/health":
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = _rate_buckets[client_ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= settings.RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                {"code": 429, "message": "请求过于频繁，请稍后再试"},
                status_code=429,
            )
        bucket.append(now)

    # ---- 鉴权（仅 DEMO_MODE=false 生效，§5.3/§10）----
    user = None
    if not settings.DEMO_MODE:
        from app.core.auth import (
            check_module_permission,
            current_user,
            is_authenticated,
        )

        user = current_user(request)
        if _is_public(path, method):
            pass  # 公开
        elif path.startswith("/api/admin"):
            if user.get("role") != "admin":
                code = 401 if not is_authenticated(user) else 403
                return JSONResponse(
                    {"code": code, "message": "需要 admin 角色"},
                    status_code=code,
                )
        elif path.startswith("/api/modules/") and path.endswith("/invoke"):
            if not is_authenticated(user):
                return JSONResponse(
                    {"code": 401, "message": "未登录或登录已过期"},
                    status_code=401,
                )
            # /api/modules/{code}/invoke → code 提取
            code = path[len("/api/modules/"):-len("/invoke")]
            if not check_module_permission(user, code):
                return JSONResponse(
                    {"code": 403, "message": f"当前账号无权访问模块 {code}"},
                    status_code=403,
                )
        elif path == "/api/files/upload":
            if not is_authenticated(user):
                return JSONResponse(
                    {"code": 401, "message": "未登录或登录已过期"},
                    status_code=401,
                )
        # 其余未匹配路径默认放行（只读形态）

    if user is not None and user.get("id"):
        request.state.user = user
        token = audit.set_current_user(int(user["id"]))
        try:
            response = await call_next(request)
        finally:
            audit.reset_current_user(token)
        return response

    response = await call_next(request)
    return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="mfg-ai-base",
        version="0.2.0",
        description="制造业 AI 应用底座（M3 生产化）",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 鉴权 + 限流中间件
    @app.middleware("http")
    async def platform_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        return await _dispatch_common(request, call_next)

    # 平台级路由
    app.include_router(health_api.router, prefix="/api")
    app.include_router(registry_api.router, prefix="/api")
    app.include_router(files_api.router, prefix="/api")
    app.include_router(auth_api.router, prefix="/api")
    app.include_router(admin_api.router, prefix="/api")

    # 根路由
    @app.get("/")
    def root() -> dict:
        return {
            "code": 0,
            "message": "ok",
            "data": {
                "service": "mfg-ai-base",
                "version": "0.2.0",
                "demo_mode": settings.DEMO_MODE,
                "docs": "/docs",
            },
        }

    return app


app = create_app()
