"""M04 SOP 问答模块业务逻辑（§9.4）。

真实 RAG 链路（M2 DoD 要求，不走 demo 短路）：

1. 输入解析：question（口语化）+ line（选填产线过滤）
2. query 改写：基于术语表把口语词替换为专业术语，提升 BM25 召回
3. BM25 检索：从 seed/knowledge/ 加载 chunks，按 query 检索 top_k
4. 版本过滤：按文档名分组，仅保留每文档最高版本；命中废止版本时标注并提示现行版本
5. 重排：BM25 分数 + 条款号命中 + 版本活性加权
6. LLM 生成：构造 system + user prompt，调用 llm_gateway.chat_stream 流式输出
7. 引用回填：从检索结果构造 citation 列表，与 LLM 输出同步流式发送
8. 检索为空：明确提示「知识库中未找到相关内容」，不调用 LLM（P6）

SSE 事件序列：meta → chunk* → citation* → result → done
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import yaml
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core import audit
from app.core.llm_gateway import get_gateway
from app.core.rag.retriever import get_service
from app.core.rag.reranker import rerank

logger = logging.getLogger(__name__)

MODULE_CODE = "m04_sop_qa"
MODULE_DIR = Path(__file__).resolve().parent

# 集合名（与 manifest.retrieval.collections 一致）
COLLECTIONS = ["m04_sop_qa"]
TOP_K = 8
RERANK_TOP_N = 5


# ===== manifest 加载（与 _template 同款写法）=====

def load_manifest() -> dict:
    p = MODULE_DIR / "manifest.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ===== 术语表加载（懒加载 + 缓存）=====

_terminology: Optional[Dict[str, List[str]]] = None
_terminology_mtime: float = 0.0


def _load_terminology() -> Dict[str, List[str]]:
    """加载 backend/config/terminology.yaml。

    支持热更新（按 mtime 失效缓存）。
    """
    global _terminology, _terminology_mtime
    # 从 settings.BASE_DIR 推导
    p = Path(settings.BASE_DIR).resolve() / "config" / "terminology.yaml"
    if not p.exists():
        return {}
    try:
        mtime = p.stat().st_mtime
        if _terminology is not None and mtime == _terminology_mtime:
            return _terminology or {}
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        # 规范化：值可能是字符串或列表
        out: Dict[str, List[str]] = {}
        for k, v in (raw or {}).items():
            if isinstance(v, list):
                out[k] = [str(x) for x in v]
            elif isinstance(v, str):
                out[k] = [v]
        _terminology = out
        _terminology_mtime = mtime
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("terminology load failed: %s", e)
        return {}


def rewrite_query(question: str) -> str:
    """query 改写：把口语词替换为专业术语，提升检索召回。

    策略：保留原 query，追加所有命中的专业术语（避免破坏原始语义）。
    """
    if not question:
        return question
    terms = _load_terminology()
    if not terms:
        return question
    extras: List[str] = []
    for oral, pro_list in terms.items():
        if oral in question:
            for pro in pro_list:
                if pro not in question:
                    extras.append(pro)
    if not extras:
        return question
    return f"{question} {' '.join(extras)}"


# ===== 版本过滤（§9.4 核心卖点）=====


def _version_key(version: str) -> Tuple[int, ...]:
    """把 V3.2 转为 (3, 2) 用于比较。"""
    s = (version or "").lstrip("Vv")
    parts = re.split(r"[.\-]", s)
    out: List[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            # 非数字片段忽略
            continue
    return tuple(out) if out else (0,)


def filter_versions(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """版本过滤：按 doc 名分组，仅保留 active 状态或最新版本。

    返回：(active_chunks, deprecated_warnings)
    - active_chunks: 用于 LLM 生成答案的 chunks
    - deprecated_warnings: 命中的废止版本信息（用于在答案中追加提示）

    规则：
    - 若同一 doc 名同时存在 active 与 deprecated，仅保留 active，但 deprecated 进入警告列表
    - 若某 doc 只有 deprecated 版本，保留 deprecated（提示现行版本由 replaced_by 指明）
    """
    if not chunks:
        return [], []
    # 按 doc 名分组
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in chunks:
        doc = c.get("doc", "")
        groups.setdefault(doc, []).append(c)

    active_out: List[Dict[str, Any]] = []
    deprecated_out: List[Dict[str, Any]] = []

    for doc, items in groups.items():
        # 找 active
        actives = [c for c in items if str(c.get("status", "active")).lower() == "active"]
        deprecates = [c for c in items if str(c.get("status", "")).lower() == "deprecated"]

        if actives:
            active_out.extend(actives)
            # 同 doc 也有 deprecated → 进警告
            for d in deprecates:
                # 只取一条作为警告（避免重复）
                if not any(w.get("doc") == doc and w.get("version") == d.get("version") for w in deprecated_out):
                    deprecated_out.append(d)
        elif deprecates:
            # 只有 deprecated 版本：保留 deprecated（标注说明），并加入警告
            # 取最新 deprecated
            deprecates.sort(key=lambda c: _version_key(c.get("version", "")), reverse=True)
            latest = deprecates[0]
            active_out.append(latest)  # 仍送入 LLM，由 LLM 在答案中提示
            deprecated_out.append(latest)
    return active_out, deprecated_out


# ===== 引用构造 =====


def build_citation(chunk: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """从 chunk 构造一个引用对象。"""
    return {
        "doc": chunk.get("doc", ""),
        "version": chunk.get("version", ""),
        "effective_date": chunk.get("effective_date", ""),
        "page": int(chunk.get("page", 1) or 1),
        "clause": str(chunk.get("clause", "")),
        "status": chunk.get("status", "active"),
        "note": note,
    }


def build_deprecated_note(chunk: Dict[str, Any]) -> str:
    """构造废止提示文案。"""
    date = chunk.get("deprecated_date") or "未知日期"
    replaced = chunk.get("replaced_by") or "未知版本"
    return f"该条款已于 {date} 废止，现行版本为 {replaced}"


# ===== Prompt 构造 =====


def _format_chunks(chunks: List[Dict[str, Any]]) -> str:
    """把检索到的 chunks 格式化为提示词中的「可用条款片段」。"""
    if not chunks:
        return "（无可用片段）"
    lines: List[str] = []
    for i, c in enumerate(chunks, 1):
        lines.append(
            f"[片段{i}] 文档：{c.get('doc', '')} | 版本：{c.get('version', '')} "
            f"| 状态：{c.get('status', '')} | 生效：{c.get('effective_date', '')} "
            f"| 条款：{c.get('clause', '')} | 页码：{c.get('page', 1)}\n"
            f"{c.get('content', '')}"
        )
    return "\n\n".join(lines)


def _format_terminology() -> str:
    """把术语表格式化为提示词片段。"""
    terms = _load_terminology()
    if not terms:
        return "（无）"
    lines = []
    for oral, pro in terms.items():
        lines.append(f"{oral} → {'/'.join(pro)}")
    return "\n".join(lines)


def _load_prompt(name: str) -> str:
    """加载 prompts/system.md 或 user.md。"""
    p = MODULE_DIR / "prompts" / name
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def build_messages(question: str, chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """构造 LLM messages。"""
    system = _load_prompt("system.md")
    user_tmpl = _load_prompt("user.md")
    user = user_tmpl.replace("{question}", question).replace(
        "{terminology}", _format_terminology()
    ).replace("{chunks}", _format_chunks(chunks))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ===== SSE 工具（与 _template 一致）=====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_chunks(text: str, delay: float = 0.03) -> AsyncIterator[str]:
    i = 0
    while i < len(text):
        chunk = text[i : i + 4]
        yield sse_event("chunk", {"text": chunk})
        i += 4
        await asyncio.sleep(delay)


# ===== DEMO 剧本降级（仅在 LLM 不可用时使用）=====

def _load_demo_scripts() -> list[dict]:
    p = MODULE_DIR / "seed" / "demo_scripts.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _match_demo_script(question: str) -> Optional[dict]:
    """按关键词匹配剧本（仅 LLM 不可用时降级使用）。"""
    scripts = _load_demo_scripts()
    if not scripts or not question:
        return None
    for s in scripts:
        for kw in s.get("keywords", []):
            if kw in question:
                return s
    return None


# ===== 主入口 =====


async def handle(payload: dict, request: Request) -> StreamingResponse:
    """模块统一 invoke 入口。

    真实 RAG 链路（M2 DoD 要求，不走 demo 短路）：
    1. 输入解析
    2. query 改写（术语表）
    3. BM25 检索
    4. 版本过滤
    5. 重排
    6. LLM 生成（带引用）
    7. 检索为空 → 直接返回 P6 提示，不调 LLM
    """
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)
    question = str(inputs.get("question", "")).strip()
    line = str(inputs.get("line", "") or "").strip()

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
                "real_rag": True,  # 标识走真实链路
            },
        )

        try:
            if not question:
                msg = "请输入问题后再提交。"
                yield sse_event("chunk", {"text": msg})
                yield sse_event("result", {"structured": {"answer": msg, "citations": []}})
                yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms, "tokens": {"in": 0, "out": 0}})
                return

            # ===== ① query 改写 =====
            rewritten = rewrite_query(question)
            logger.info("[M04] question=%r rewritten=%r", question, rewritten)

            # ===== ② BM25 检索 =====
            service = get_service()
            hits = service.search(rewritten, COLLECTIONS, top_k=TOP_K)
            logger.info("[M04] BM25 hits=%d", len(hits))

            # ===== ③ 版本过滤 =====
            active_chunks, deprecated_warnings = filter_versions(hits)

            # ===== ④ 重排 =====
            reranked = rerank(rewritten, active_chunks, top_n=RERANK_TOP_N)
            logger.info("[M04] reranked=%d deprecated=%d", len(reranked), len(deprecated_warnings))

            # ===== ⑤ 检索为空 → P6 提示，不调 LLM =====
            if not reranked:
                msg = "知识库中未找到相关内容，请补充 SOP 文档或联系工艺工程师。"
                if stream:
                    async for piece in _stream_chunks(msg):
                        yield piece
                else:
                    yield sse_event("chunk", {"text": msg})
                yield sse_event(
                    "result",
                    {"structured": {"answer": msg, "citations": [], "related": []}},
                )
                latency = audit.now_ms() - start_ms
                yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                audit.audit_log(
                    module_code=MODULE_CODE,
                    action="invoke",
                    question=question,
                    answer=msg,
                    latency_ms=latency,
                    status="success",  # 检索为空是正确行为，不算 failed
                    trace_id=trace_id,
                )
                return

            # ===== ⑥ 构造引用 =====
            citations: List[Dict[str, Any]] = []
            deprecated_docs = {w.get("doc", "") for w in deprecated_warnings}

            # 先发 deprecated 警告引用（让前端能优先展示版本提示）
            for w in deprecated_warnings:
                note = build_deprecated_note(w)
                cite = build_citation(w, note=note)
                citations.append(cite)
                yield sse_event("citation", cite)

            # 再发 active 引用
            for c in reranked:
                if c.get("doc", "") in deprecated_docs and str(c.get("status", "")).lower() == "deprecated":
                    # 已在 deprecated_warnings 中发过
                    continue
                cite = build_citation(c)
                citations.append(cite)
                yield sse_event("citation", cite)

            # ===== ⑦ LLM 生成（流式） =====
            messages = build_messages(question, reranked)

            # LLM 不可用 → 降级到 demo 剧本
            answer_text = ""
            llm_used = False
            if settings.llm_ready:
                try:
                    gateway = get_gateway()
                    collected: List[str] = []
                    for piece in gateway.chat_stream(
                        messages,
                        module_code=MODULE_CODE,
                        temperature=0.2,
                        max_tokens=1500,
                    ):
                        collected.append(piece)
                        if stream:
                            yield sse_event("chunk", {"text": piece})
                    answer_text = "".join(collected)
                    llm_used = True
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M04] LLM stream failed, fallback to demo script: %s", e)
                    llm_used = False

            if not llm_used:
                # 降级：匹配 demo 剧本；命中则流式输出剧本答案
                scripted = _match_demo_script(question)
                if scripted:
                    answer_text = scripted.get("answer", "")
                    if stream and answer_text:
                        async for piece in _stream_chunks(answer_text):
                            yield piece
                    else:
                        yield sse_event("chunk", {"text": answer_text})
                else:
                    # 兜底：把命中的 chunks 内容直接拼接返回（保证 P6 不编造）
                    fallback_lines = ["（LLM 不可用，以下为检索到的原始条款）"]
                    for c in reranked:
                        fallback_lines.append(
                            f"[{c.get('doc', '')} {c.get('version', '')} 条款 {c.get('clause', '')} 第{c.get('page', 1)}页]\n{c.get('content', '')}"
                        )
                    answer_text = "\n\n".join(fallback_lines)
                    if stream:
                        async for piece in _stream_chunks(answer_text):
                            yield piece
                    else:
                        yield sse_event("chunk", {"text": answer_text})

            # ===== ⑧ result 事件：结构化输出 =====
            related = [
                {"doc": c.get("doc", ""), "version": c.get("version", ""), "clause": c.get("clause", "")}
                for c in reranked[:3]
            ]
            yield sse_event(
                "result",
                {
                    "structured": {
                        "answer": answer_text,
                        "citations": citations,
                        "related": related,
                        "query_rewritten": rewritten if rewritten != question else None,
                        "llm_used": llm_used,
                    }
                },
            )

            # ===== ⑨ done =====
            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=question,
                answer=answer_text[:4000],
                citations=citations,
                latency_ms=latency,
                status="success" if llm_used else "degraded",
                trace_id=trace_id,
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("[M04] handle failed: %s", e)
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=question,
                answer=str(e),
                status="failed",
                trace_id=trace_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream")
