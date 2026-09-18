"""M05 生产数据问答模块的 Pydantic 输入输出模型。

仅用于校验 invoke 请求体；output_schema 由 manifest 声明，前端按其渲染。
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel


class InvokeRequest(BaseModel):
    inputs: dict
    stream: bool = True
    options: dict = {}


class Attribution(BaseModel):
    factor: str
    evidence: str = ""
    impact: str = ""


class M05Output(BaseModel):
    sql: str = ""
    table_data: List[Dict[str, Any]] = []
    chart_spec: Dict[str, Any] = {}
    insight: str = ""
    attribution: List[Attribution] = []
    note: str = ""
