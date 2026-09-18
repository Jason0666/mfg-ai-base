"""POST /api/guest/verify（§2.1）：访客令牌校验（免鉴权，前端落地时调用）。

返回 code=0 + 剩余时效/配额信息；失败时 code!=0 并给出 reason：
invalid（签名无效）/ expired（已过期）/ revoked（已吊销）/ quota（超次）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.core import guest

router = APIRouter()
logger = logging.getLogger(__name__)

_REASONS = {
    "invalid": "访问链接无效",
    "expired": "本次演示访问已结束（链接已过期）",
    "revoked": "本次演示访问已结束（链接已被吊销）",
    "quota": "本次演示访问已结束（调用次数已用完）",
}


class GuestVerifyBody(BaseModel):
    token: str


@router.post("/guest/verify")
def guest_verify(body: GuestVerifyBody) -> dict:
    err, info = guest.check(body.token or "")
    if err:
        return {"code": 1, "reason": err, "message": _REASONS.get(err, "访问链接无效")}
    return {"code": 0, "message": "ok", "data": info}
