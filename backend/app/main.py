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
    # 生产模式安全校验：SECRET_KEY 缺失/弱密钥直接拒绝启动（fail-fast）
    settings.validate_security()
    # 平台四表（任何模式都需要：审计持久化不依赖 DEMO）
    try:
        from app.core.db import (
            bootstrap_users,
            ensure_platform_tables,
            seed_audit_logs,
            sync_modules,
        )

        ensure_platform_tables()
        bootstrap_users()
        seed_audit_logs()
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

# 免鉴权路径（§2.1：除 login/verify 外所有 /api/ 均须校验令牌；health/docs 除外）
_OPEN_EXACT = {"/", "/openapi.json", "/favicon.ico", "/api/health", "/api/auth/login", "/api/guest/verify"}
_OPEN_PREFIX = ("/docs", "/redoc")

_GUEST_ERR_MSG = {
    "invalid": "访问链接无效",
    "expired": "本次演示访问已结束（链接已过期）",
    "revoked": "本次演示访问已结束（链接已被吊销）",
    "quota": "本次演示访问已结束（调用次数已用完）",
}


def _extract_guest_token(request: Request) -> str:
    """访客令牌提取：URL ?t=（导出下载直达）→ X-Guest-Token 头 → Bearer。"""
    t = request.query_params.get("t")
    if t:
        return t.strip()
    t = request.headers.get("X-Guest-Token")
    if t:
        return t.strip()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


async def _dispatch_common(request: Request, call_next):  # type: ignore[no-untyped-def]
    """限流 + 统一门禁（§2.1）：平台用户（admin JWT）与访客令牌双轨校验。

    - 免鉴权：/api/health、/api/auth/login、/api/guest/verify、非 /api/ 路径
    - 平台用户：/api/admin/* 仅 admin；模块 invoke 走模块级权限
    - 访客令牌：四项校验（签名/有效期/吊销/配额）+ 白名单路径 +
      invoke 频率限制（5/分钟）+ 次数扣减 + 剧本模式透传（高级访客除外）
    - 匿名：401
    """
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

    # 非 /api/ 路径（首页 / docs / 静态资源）放行
    if not path.startswith("/api/"):
        return await call_next(request)

    # 公开接口
    if path in _OPEN_EXACT or any(path.startswith(p) for p in _OPEN_PREFIX):
        return await call_next(request)

    from app.core import guest as guest_core
    from app.core.auth import check_module_permission, current_user, is_authenticated

    # ---- 1) 平台用户身份 ----
    user = current_user(request)
    if is_authenticated(user):
        if path.startswith("/api/admin"):
            if user.get("role") != "admin":
                return JSONResponse(
                    {"code": 403, "reason": "forbidden", "message": "需要 admin 角色"},
                    status_code=403,
                )
        elif path.startswith("/api/modules/") and path.endswith("/invoke"):
            code = path[len("/api/modules/"):-len("/invoke")]
            if not check_module_permission(user, code):
                return JSONResponse(
                    {"code": 403, "reason": "forbidden",
                     "message": f"当前账号无权访问模块 {code}"},
                    status_code=403,
                )
        request.state.user = user
        token = audit.set_current_user(int(user["id"]))
        try:
            response = await call_next(request)
        finally:
            audit.reset_current_user(token)
        return response

    # ---- 2) 访客令牌（四项校验 + 白名单 + 三重防消耗）----
    gtoken = _extract_guest_token(request)
    if gtoken:
        err, ginfo = guest_core.check(gtoken)
        if err:
            status = 429 if err == "quota" else 401
            return JSONResponse(
                {"code": status, "reason": err,
                 "message": _GUEST_ERR_MSG.get(err, "访问链接无效")},
                status_code=status,
            )
        # 访客白名单：模块列表 / 模块只读 GET（sample 等）/ 模块 invoke / 导出下载
        allowed = (
            (
                method == "GET"
                and (
                    path.startswith("/api/registry")
                    or path.startswith("/api/modules/")
                    or path.startswith("/api/files/")
                )
            )
            or (
                method == "POST"
                and path.startswith("/api/modules/")
                and path.endswith("/invoke")
            )
        )
        if not allowed:
            return JSONResponse(
                {"code": 403, "reason": "forbidden", "message": "访客无权访问该接口"},
                status_code=403,
            )
        request.state.guest = ginfo
        if method == "POST" and path.endswith("/invoke"):
            # 频率限制（三重保险之三）
            if not guest_core.allow_invoke(ginfo["jti"]):
                return JSONResponse(
                    {"code": 429, "reason": "rate",
                     "message": "操作过于频繁，请稍后再试（每分钟最多 5 次调用）"},
                    status_code=429,
                )
            # 次数扣减（三重保险之二；配额已在 check() 预校验）
            guest_core.incr_used(ginfo["jti"])
            # 降级策略（§2.1）：默认强制剧本模式；高级访客在真实 LLM 配额内放行真实链路
            real_allowed = bool(ginfo.get("allow_real_llm")) and (
                int(ginfo.get("real_llm_used") or 0) < int(ginfo.get("real_llm_max") or 0)
            )
            if real_allowed:
                guest_core.incr_real_used(ginfo["jti"])
            request.state.guest_force_script = not real_allowed
        else:
            request.state.guest_force_script = True
        return await call_next(request)

    # ---- 3) 匿名请求：一律 401 ----
    return JSONResponse(
        {"code": 401, "reason": "unauthorized", "message": "未登录或缺少访问凭证"},
        status_code=401,
    )


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
    from app.api import guest as guest_api
    app.include_router(guest_api.router, prefix="/api")

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
