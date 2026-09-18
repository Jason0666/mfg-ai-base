"""M06 能耗异常分析模块（DoD 验证：按模块开发规范创建）。

演示最小可用逻辑：根据输入问题构造结构化回答。
真实场景下这里会接 EMS 数据库查询 + LLM 归因。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
MODULE_CODE = "m06_energy_analysis"
MODULE_DIR = Path(__file__).resolve().parent


# 模拟能耗数据（真实场景从 EMS 数据库查询）
_MOCK_DATA = [
    {"date": "2026-09-11", "line": "A线", "value": 1850, "reason": "注塑机保温层老化，散热增大"},
    {"date": "2026-09-12", "line": "A线", "value": 1620, "reason": "正常"},
    {"date": "2026-09-13", "line": "B线", "value": 1480, "reason": "正常"},
    {"date": "2026-09-14", "line": "A线", "value": 1910, "reason": "空压机排气压力偏高 0.7MPa，超设定 0.1MPa"},
    {"date": "2026-09-15", "line": "C线", "value": 1320, "reason": "正常"},
]
_NORMAL_THRESHOLD = 1700


async def handle(payload: dict, request: Request) -> StreamingResponse:
    """模块统一 invoke 入口。"""
    inputs = payload.get("inputs", {}) or {}
    question = str(inputs.get("question", "")).strip()
    line = str(inputs.get("line", "全部") or "全部").strip()

    # 1. 筛选数据
    data = _MOCK_DATA
    if line and line != "全部":
        data = [d for d in _MOCK_DATA if d["line"] == line]

    # 2. 找异常时段（超阈值）
    anomalies = [
        {"date": d["date"], "line": d["line"], "value": d["value"], "reason": d["reason"]}
        for d in data
        if d["value"] > _NORMAL_THRESHOLD
    ]

    # 3. 生成结论
    if anomalies:
        summary = f"在查询范围内发现 {len(anomalies)} 个异常时段，能耗超过阈值 {_NORMAL_THRESHOLD}kWh。"
    else:
        summary = f"在查询范围内未发现能耗异常（阈值 {_NORMAL_THRESHOLD}kWh）。"

    # 4. 节能建议
    suggestions = []
    if any("保温" in a["reason"] for a in anomalies):
        suggestions.append("检查并更换注塑机保温层，减少散热损失")
    if any("空压" in a["reason"] for a in anomalies):
        suggestions.append("校准空压机排气压力设定值，每降 0.1MPa 节能约 6%")
    if not suggestions:
        suggestions.append("当前能耗正常，建议持续监测")

    # 5. 构造结构化结果
    structured: Dict[str, Any] = {
        "summary": summary,
        "anomalies": anomalies,
        "suggestions": suggestions,
    }

    # 6. SSE 返回
    async def event_stream() -> AsyncIterator[str]:
        yield f"event: meta\ndata: {json.dumps({'module': MODULE_CODE})}\n\n"
        yield f"event: result\ndata: {json.dumps({'structured': structured}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
