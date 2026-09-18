"""M04 SOP 问答模块的 Pydantic 输入输出模型。

仅用于校验 invoke 请求体；output_schema 由 manifest 声明，前端按其渲染。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class InvokeRequest(BaseModel):
    inputs: dict
    stream: bool = True
    options: dict = {}


class Citation(BaseModel):
    doc: str
    version: str = ""
    effective_date: str = ""
    page: Optional[int] = None
    clause: Optional[str] = None
    status: str = "active"
    note: str = ""


class RelatedItem(BaseModel):
    doc: str
    version: str = ""
    clause: str = ""


class M04Output(BaseModel):
    answer: str
    citations: List[Citation] = []
    related: List[RelatedItem] = []
