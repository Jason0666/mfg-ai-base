"""鉴权与角色（§6.1 app_user/app_module_permission，M3 实现）。

角色：admin | analyst | viewer
- DEMO 模式关闭鉴权（§5.3）：current_user 返回匿名 viewer
- PROD 模式：Authorization: Bearer <HS256 JWT>
口令哈希：pbkdf2_hmac-sha256（stdlib，无第三方依赖）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_PBKDF2_ITERS = 120_000
_TOKEN_TTL_SECONDS = 12 * 3600  # 12h


# ---------------------------------------------------------------------------
# 口令哈希（pbkdf2$iters$salt_b64$hash_b64）
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ITERS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        iters = int(iters_s)
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expect = base64.urlsafe_b64decode(hash_b64 + "=" * (-len(hash_b64) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(actual, expect)
    except Exception:  # noqa: BLE001 格式不符一律拒绝
        return False


# ---------------------------------------------------------------------------
# JWT（HS256，stdlib 手写，避免引入 PyJWT）
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(
    uid: int,
    username: str,
    role: str,
    ttl_seconds: int = _TOKEN_TTL_SECONDS,
) -> str:
    """签发 HS256 JWT：header.payload.signature。"""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "uid": uid,
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """校验签名与 exp；失败返回 None。"""
    try:
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        expect = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expect, _b64url_decode(s)):
            return None
        payload = json.loads(_b64url_decode(p))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 请求身份与权限
# ---------------------------------------------------------------------------
ANONYMOUS = {"id": 0, "username": "anonymous", "role": "viewer"}


def current_user(request) -> Dict[str, Any]:  # type: ignore[no-untyped-def]
    """从 Authorization: Bearer 解析身份。

    两种模式均优先解析有效 Bearer JWT（DEMO 下管理员登录后可正确归因审计）；
    guest 令牌（role=guest）不是用户身份，一律视为匿名（访客由 core.guest 单独校验）。
    解析失败：DEMO 模式返回匿名 viewer；PROD 模式同样返回匿名
    （是否拒绝由中间件按路径白名单决定）。
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header[7:].strip())
        if payload and payload.get("role") != "guest":
            return {
                "id": int(payload.get("uid", 0)),
                "username": str(payload.get("username", "")),
                "role": str(payload.get("role", "viewer")),
            }
    return dict(ANONYMOUS)


def is_authenticated(user: Dict[str, Any]) -> bool:
    return bool(user.get("id"))


def require_role(*roles: str):  # type: ignore[no-untyped-def]
    """FastAPI 依赖工厂（生产模式校验角色；DEMO 放行）。"""
    from fastapi import HTTPException

    def _dep(request) -> Dict[str, Any]:  # type: ignore[no-untyped-def]
        user = current_user(request)
        if not settings.DEMO_MODE and user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return _dep


def check_module_permission(user: Dict[str, Any], module_code: str) -> bool:
    """viewer 走 app_module_permission 白名单（白名单空 = 全放行）；admin/analyst 全放行。"""
    if user.get("role") in ("admin", "analyst"):
        return True
    from app.core.db import get_user_permissions

    granted = get_user_permissions(int(user.get("id", 0)))
    return not granted or module_code in granted
