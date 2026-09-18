"""POST /api/auth/login, GET /api/auth/me（§7.1，M3 实现）。

DEMO 模式（§5.3）业务接口关闭鉴权，但登录接口本身两种模式都真实可用
（种子用户在两种模式下都会建好，便于演示登录页与审计归因）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import settings
from app.core import audit
from app.core.auth import current_user, issue_token, verify_password
from app.core.db import get_user_by_username

router = APIRouter()
logger = logging.getLogger(__name__)


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request) -> dict:
    user = get_user_by_username(body.username.strip())
    if (
        user is None
        or not user.get("enabled", 0)
        or not verify_password(body.password, user.get("password_hash", ""))
    ):
        # 登录失败也留痕（不含口令）
        audit.audit_log(
            user_id=None,
            module_code="",
            action="login",
            question=f"login attempt: {body.username.strip()}",
            answer="failed",
            status="failed",
        )
        return {"code": 1, "message": "用户名或密码错误"}

    token = issue_token(int(user["id"]), user["username"], user["role"])
    audit.audit_log(
        user_id=int(user["id"]),
        module_code="",
        action="login",
        question=f"login: {user['username']}",
        answer="ok",
        status="success",
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "token": token,
            "user": {
                "id": int(user["id"]),
                "username": user["username"],
                "display_name": user.get("display_name") or user["username"],
                "role": user["role"],
            },
        },
    }


@router.get("/auth/me")
def me(request: Request) -> dict:
    user = current_user(request)
    if not settings.DEMO_MODE and not user.get("id"):
        return {"code": 1, "message": "auth required"}
    return {"code": 0, "message": "ok", "data": user}
