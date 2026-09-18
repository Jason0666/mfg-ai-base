"""M02 招标文件智能审核的 Pydantic 输入模型。"""
from __future__ import annotations

from pydantic import BaseModel


class InvokeRequest(BaseModel):
    inputs: dict
    stream: bool = True
    options: dict = {}
