"""M03 质量异常分析与 8D 报告模块的 Pydantic 输入模型。

仅用于校验 invoke 请求体；output_schema 由 manifest 声明，前端按其渲染。
"""
from __future__ import annotations

from pydantic import BaseModel


class InvokeRequest(BaseModel):
    inputs: dict
    stream: bool = True
    options: dict = {}
