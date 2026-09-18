"""_template 模块的 Pydantic 输入输出模型。

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
    page: Optional[int] = None
    clause: Optional[str] = None


class TemplateOutput(BaseModel):
    answer: str
    citations: List[Citation] = []
