"""_template 模块 API 路由。

§8.2 标准骨架：所有模块的 api.py 都按这个写法复制。
路由前缀由 registry 挂载到 /api/modules/{code}。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .logic import handle

router = APIRouter()


@router.post("/invoke")
async def invoke(request: Request):
    payload = await request.json()
    return await handle(payload, request)
