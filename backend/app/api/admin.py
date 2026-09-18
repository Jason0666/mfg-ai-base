"""管理接口（§7.1 + §2.1 访客链接管理）。

- GET /api/admin/audit、/api/admin/stats：审计与统计
- POST/GET /api/admin/guest-tokens、DELETE /api/admin/guest-tokens/{jti}：访客时效链接管理
以上接口由 main.py 中间件强制 admin 角色（两种运行模式一致）。
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core import audit, guest

router = APIRouter()


@router.get("/admin/audit")
def get_audit(
    module: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    data = audit.query_audit(
        module=module or None,
        status=status or None,
        action=action or None,
        q=q or None,
        limit=limit,
        offset=offset,
    )
    return {"code": 0, "message": "ok", "data": data}


@router.get("/admin/stats")
def get_stats() -> dict:
    return {"code": 0, "message": "ok", "data": audit.stats()}


# ---------------------------------------------------------------------------
# 访客时效链接（§2.1）
# ---------------------------------------------------------------------------
class GuestTokenBody(BaseModel):
    note: str = ""
    hours: int = Field(default=1, ge=1, le=720)  # 1 / 6 / 24
    max_calls: int = Field(default=0, ge=0, le=100000)  # 0=用默认 30
    allow_real_llm: bool = False  # 高级访客：允许消耗真实 LLM 配额
    real_llm_max: int = Field(default=0, ge=0, le=10000)  # 0=用默认 10


def _token_view(row: dict) -> dict:
    now = int(time.time())
    exp = int(row["exp"])
    used = int(row["used_calls"])
    if row.get("revoked"):
        status = "revoked"
    elif now >= exp:
        status = "expired"
    elif used >= int(row["max_calls"]):
        status = "quota"
    else:
        status = "active"
    return {
        "jti": row["jti"],
        "note": row.get("note") or "",
        "exp": exp,
        "max_calls": int(row["max_calls"]),
        "used_calls": used,
        "remaining_calls": max(0, int(row["max_calls"]) - used),
        "allow_real_llm": bool(row.get("allow_real_llm")),
        "real_llm_max": int(row.get("real_llm_max") or 0),
        "real_llm_used": int(row.get("real_llm_used") or 0),
        "status": status,
        "created_at": row.get("created_at"),
    }


@router.post("/admin/guest-tokens")
def create_guest_token(body: GuestTokenBody) -> dict:
    row = guest.create_token(
        note=body.note.strip(),
        hours=body.hours,
        max_calls=body.max_calls,
        allow_real_llm=body.allow_real_llm,
        real_llm_max=body.real_llm_max,
    )
    view = _token_view(row)
    view["token"] = row["token"]  # 仅创建时返回一次明文 JWT
    audit.audit_log(
        module_code="", action="guest_token_create",
        question=body.note.strip() or "(未备注)",
        answer=view["jti"], status="success",
    )
    return {"code": 0, "message": "ok", "data": view}


@router.get("/admin/guest-tokens")
def list_guest_tokens() -> dict:
    items = [_token_view(r) for r in guest.list_tokens()]
    return {"code": 0, "message": "ok", "data": {"items": items}}


@router.delete("/admin/guest-tokens/{jti}")
def revoke_guest_token(jti: str) -> dict:
    ok = guest.revoke(jti)
    audit.audit_log(
        module_code="", action="guest_token_revoke",
        question=jti, answer="ok" if ok else "not_found",
        status="success" if ok else "failed",
    )
    if not ok:
        return {"code": 1, "message": "令牌不存在或已吊销"}
    return {"code": 0, "message": "已吊销，立即失效"}
