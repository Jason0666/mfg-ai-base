"""M06 能耗异常分析模块（示例模块 / 平台剧本驱动）。

本模块不接入真实 EMS、不调用 LLM：
- 剧本模式（DEMO_MODE 且无 LLM Key）：结果来自 seed/demo_scripts.json，
  经平台级剧本匹配器（core/demo_script）按问题关键词匹配后模拟流式输出，
  返回结构与 output_schema 完全一致；未命中剧本时给出明确提示，不静默失败。
- 配置了 LLM Key 时：未命中剧本的自由提问保持改造前行为，返回内置通用示例分析。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.core import audit, demo_script

logger = logging.getLogger(__name__)
MODULE_CODE = "m06_energy_analysis"
MODULE_DIR = Path(__file__).resolve().parent


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _empty_structured(note: str) -> Dict[str, Any]:
    return {
        "notice": note,
        "summary": note,
        "anomalies": [],
        "suggestions": [],
        "data_source": "模拟示例数据",
    }


# 内置示例能耗数据（本模块为示例模块，不接真实 EMS；保留通用兜底分析，
# 保证配置 LLM Key 后自由提问行为与改造前一致，不被剧本分支替换）
_FALLBACK_DATA = [
    {"date": "2026-09-11", "line": "A线", "value": 1850, "reason": "注塑机保温层老化，散热增大"},
    {"date": "2026-09-12", "line": "A线", "value": 1620, "reason": "正常"},
    {"date": "2026-09-13", "line": "B线", "value": 1480, "reason": "正常"},
    {"date": "2026-09-14", "line": "A线", "value": 1910, "reason": "空压机排气压力偏高 0.7MPa，超设定 0.1MPa"},
    {"date": "2026-09-15", "line": "C线", "value": 1320, "reason": "正常"},
]
_FALLBACK_THRESHOLD = 1700


def _generic_structured() -> Dict[str, Any]:
    anomalies = [
        {"date": d["date"], "line": d["line"], "value": d["value"], "reason": d["reason"]}
        for d in _FALLBACK_DATA if d["value"] > _FALLBACK_THRESHOLD
    ]
    suggestions = []
    if any("保温" in a["reason"] for a in anomalies):
        suggestions.append("检查并更换注塑机保温层，减少散热损失")
    if any("空压" in a["reason"] for a in anomalies):
        suggestions.append("校准空压机排气压力设定值，每降 0.1MPa 节能约 6%")
    if not suggestions:
        suggestions.append("当前能耗正常，建议持续监测")
    return {
        "notice": "本模块为示例模块，当前展示内置模拟数据。",
        "summary": "在查询范围内发现 %d 个异常时段，能耗超过阈值 %dkWh。"
                   % (len(anomalies), _FALLBACK_THRESHOLD),
        "anomalies": anomalies,
        "suggestions": suggestions,
        "data_source": "模拟示例数据",
    }


async def handle(payload: dict, request: Request) -> StreamingResponse:
    """模块统一 invoke 入口（剧本模式，无 LLM 依赖）。"""
    inputs = payload.get("inputs", {}) or {}
    question = str(inputs.get("question", "")).strip()

    trace_id = audit.new_trace_id()
    start_ms = audit.now_ms()

    async def event_stream() -> AsyncIterator[str]:
        script_mode = demo_script.is_active_for_request(request)
        yield sse_event("meta", {
            "trace_id": trace_id,
            "module": MODULE_CODE,
            "demo_mode": True,
            "script_mode": script_mode,
        })

        scripted = demo_script.match(MODULE_CODE, question) if question else None

        if scripted and scripted.get("structured"):
            structured = scripted["structured"]
            answer_text = json.dumps(structured, ensure_ascii=False)
            # 模拟流式：JSON 分片，50–120ms 随机间隔
            async for piece in demo_script.simulated_chunks(answer_text, chunk_size=24):
                yield sse_event("chunk", {"text": piece})
            yield sse_event("result", {"structured": structured})
            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE, action="invoke", question=question,
                answer=answer_text[:4000], latency_ms=latency,
                status="degraded" if script_mode else "success", trace_id=trace_id,
            )
            return

        if not question:
            # 空问题：明确提示，不静默失败、不返回空
            msg = "请输入问题后再提交。"
            async for piece in demo_script.simulated_chunks(msg, chunk_size=12):
                yield sse_event("chunk", {"text": piece})
            structured = _empty_structured(msg)
            status = "success"
        elif script_mode:
            # 剧本模式（DEMO 且无 LLM Key）未命中：明确提示
            msg = demo_script.not_matched_message(MODULE_CODE)
            async for piece in demo_script.simulated_chunks(msg, chunk_size=12):
                yield sse_event("chunk", {"text": piece})
            structured = _empty_structured(msg)
            status = "degraded"
        else:
            # 配置了 LLM Key（非剧本模式）：保持改造前行为，返回内置通用示例分析
            structured = _generic_structured()
            answer_text = json.dumps(structured, ensure_ascii=False)
            async for piece in demo_script.simulated_chunks(answer_text, chunk_size=24):
                yield sse_event("chunk", {"text": piece})
            status = "success"

        yield sse_event("result", {"structured": structured})
        latency = audit.now_ms() - start_ms
        yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
        audit.audit_log(
            module_code=MODULE_CODE, action="invoke", question=question,
            answer=json.dumps(structured, ensure_ascii=False)[:4000],
            latency_ms=latency, status=status, trace_id=trace_id,
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
