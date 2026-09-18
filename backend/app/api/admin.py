"""GET /api/admin/audit, /api/admin/stats（§7.1）。

M3：审计走 DB 持久化 + 条件筛选；统计含模块分布与近 7 日趋势。
生产模式下这两个接口由 main.py 中间件强制 admin 角色。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.core import audit

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
