"""M04 SOP 问答模块 API 路由（§8.2 标准骨架）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .logic import handle

router = APIRouter()


@router.post("/invoke")
async def invoke(request: Request):
    payload = await request.json()
    return await handle(payload, request)
