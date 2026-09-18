"""M01 设备故障智能诊断模块的 Pydantic 输入输出模型。

仅用于校验 invoke 请求体；output_schema 由 manifest 声明，前端按其渲染。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class InvokeRequest(BaseModel):
    inputs: dict
    stream: bool = True
    options: dict = {}


class SourceRef(BaseModel):
    doc: str = ""
    version: str = ""
    page: Optional[int] = None
    clause: str = ""
    wo_id: str = ""


class Cause(BaseModel):
    name: str
    probability: float = 0.0
    evidence: List[str] = []
    source_refs: List[SourceRef] = []


class SparePart(BaseModel):
    name: str
    model: str = ""
    stock: int = 0


class Case(BaseModel):
    wo_id: str
    root_cause: str = ""
    solution: str = ""


class M01Output(BaseModel):
    causes: List[Cause] = []
    steps: List[str] = []
    spare_parts: List[SparePart] = []
    cases: List[Case] = []
    note: str = ""
