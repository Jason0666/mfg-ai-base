"""_template 模块业务逻辑骨架（§8.2 标准模板）。

新模块复制 _template/ 后主要改本文件。本骨架包含：
- DEMO 模式短路（命中预设剧本 → 流式输出）
- 真实链路骨架（RAG → LLM → 结构化）的占位实现
- 检索为空时不编造（P6）
- SSE 事件序列（meta → chunk → citation → result → done）
- 审计写入

注意：_template 自身不携带真实业务能力，invoke 返回的是占位内容。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import yaml
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core import audit

MODULE_CODE = "_template"
MODULE_DIR = Path(__file__).resolve().parent


# ===== manifest 加载（每个模块都用同款写法）=====

def load_manifest() -> dict:
    p = MODULE_DIR / "manifest.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ===== 演示剧本匹配（§5.3 关键机制）=====

def _load_demo_scripts() -> list[dict]:
    """读取 seed/demo_scripts.json；缺失返回空。"""
    p = MODULE_DIR / "seed" / "demo_scripts.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def match_demo_script(inputs: dict) -> Optional[dict]:
    """按关键词包含匹配演示剧本（§5.3）。"""
    scripts = _load_demo_scripts()
    if not scripts:
        return None
    q = (
        inputs.get("question")
        or inputs.get("symptom")
        or json.dumps(inputs, ensure_ascii=False)
    )
    q = str(q)
    # 优先返回完全包含匹配
    for s in scripts:
        for kw in s.get("keywords", []):
            if kw in q:
                return s
    # 次选：第一个剧本（保证 DEMO 模式有响应）
    return scripts[0] if scripts else None


# ===== SSE 工具：所有模块共享的写法 =====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_chunks(text: str, delay: float = 0.03) -> AsyncIterator[str]:
    """把文本按 3-5 字切片，模拟打字机 30-60 字/秒（§5.3）。"""
    i = 0
    while i < len(text):
        chunk = text[i : i + 4]
        yield sse_event("chunk", {"text": chunk})
        i += 4
        await asyncio.sleep(delay)


# ===== 主入口 =====

async def handle(payload: dict, request: Request) -> StreamingResponse:
    """模块统一 invoke 入口。

    流程：
    1. DEMO 模式 → 命中剧本则流式返回
    2. 未命中或非 DEMO → 真实链路（_template 为占位）
    """
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)
    options = payload.get("options", {}) or {}

    trace_id = audit.new_trace_id()
    start_ms = audit.now_ms()

    async def gen() -> AsyncIterator[str]:
        # meta
        yield sse_event(
            "meta",
            {
                "trace_id": trace_id,
                "module": MODULE_CODE,
                "demo_mode": settings.DEMO_MODE,
            },
        )

        try:
            scripted = None
            if settings.DEMO_MODE:
                scripted = match_demo_script(inputs)

            if scripted is not None:
                # DEMO 短路：流式输出剧本答案
                answer_text = scripted.get("answer", "")
                if stream and answer_text:
                    async for piece in _stream_chunks(answer_text):
                        yield piece
                else:
                    yield sse_event("chunk", {"text": answer_text})

                # citation 事件
                for c in scripted.get("citations", []):
                    yield sse_event("citation", c)

                # result 事件：结构化输出
                yield sse_event(
                    "result",
                    {"structured": scripted.get("structured", {"answer": answer_text})},
                )
            else:
                # 真实链路占位（_template 不接 RAG/LLM）
                msg = (
                    "【模板占位】_template 模块未配置真实业务能力，"
                    "请复制 _template/ 创建新模块并在 logic.py 实现业务逻辑。"
                )
                if stream:
                    async for piece in _stream_chunks(msg):
                        yield piece
                else:
                    yield sse_event("chunk", {"text": msg})
                yield sse_event("result", {"structured": {"answer": msg, "citations": []}})

            # done
            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=json.dumps(inputs, ensure_ascii=False)[:4000],
                answer="(streamed)",
                latency_ms=latency,
                status="success" if scripted else "degraded",
                trace_id=trace_id,
            )
        except Exception as e:  # noqa: BLE001
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=json.dumps(inputs, ensure_ascii=False)[:4000],
                answer=str(e),
                status="failed",
                trace_id=trace_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream")
