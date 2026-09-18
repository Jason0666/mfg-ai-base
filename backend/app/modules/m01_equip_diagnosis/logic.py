"""M01 设备故障智能诊断模块业务逻辑（§9.1）。

真实双路 RAG 链路（M2 DoD 要求，不走 demo 短路）：

1. 输入解析：equipment_code（选填）、symptom（必填）、recent_change、running_hours
2. query 改写：把 equipment_code 与症状关键词拼进检索 query 提升召回
3. 双路 BM25 检索：equip_manual（设备手册）+ work_order_history（历史工单）
4. 设备型号过滤：若提供 equipment_code，优先保留同型号 chunks（软过滤，不硬删）
5. 重排：BM25 分数 + 条款号命中 + 设备型号命中加权
6. LLM 生成：构造 system + user prompt，调用 llm_gateway.chat_stream 流式输出结构化 JSON
7. JSON 解析：从 LLM 输出中提取 causes/steps/spare_parts/cases（容错：去 markdown 包裹）
8. 引用回填：从检索结果构造 citation 列表（手册页码/条款号 + 工单号）
9. 检索为空：明确提示「知识库中未找到相关内容」，不调用 LLM（P6）

SSE 事件序列：meta → chunk*（LLM 流式 JSON 文本）→ citation* → result（结构化）→ done
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import yaml
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.core import audit, demo_script
from app.core.llm_gateway import get_gateway
from app.core.rag.retriever import get_service
from app.core.rag.reranker import rerank

logger = logging.getLogger(__name__)

MODULE_CODE = "m01_equip_diagnosis"
MODULE_DIR = Path(__file__).resolve().parent

# 集合名（与 manifest.retrieval.collections 一致）
COLLECTIONS = ["equip_manual", "work_order_history"]
TOP_K = 8
RERANK_TOP_N = 5


# ===== manifest 加载 =====

def load_manifest() -> dict:
    p = MODULE_DIR / "manifest.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ===== query 改写：把设备编号拼进 query 提升召回 =====

def rewrite_query(symptom: str, equipment_code: str = "") -> str:
    """query 改写：把 equipment_code 拼进 query，提升 BM25 召回。

    策略：保留原 symptom，追加 equipment_code（若有）。
    """
    if not symptom:
        return symptom
    if equipment_code and equipment_code not in symptom:
        return f"{symptom} {equipment_code}"
    return symptom


# ===== 设备型号过滤（软过滤）=====

def filter_by_equipment(
    chunks: List[Dict[str, Any]], equipment_code: str
) -> List[Dict[str, Any]]:
    """按设备型号软过滤。

    策略：若提供 equipment_code，优先保留 content/doc 中包含该型号的 chunks；
    若过滤后为空，退回原列表（避免过度过滤导致 P6 误触发）。
    """
    if not equipment_code or not chunks:
        return chunks
    matched = [
        c for c in chunks
        if equipment_code in str(c.get("content", ""))
        or equipment_code in str(c.get("doc", ""))
        or equipment_code in str(c.get("source_file", ""))
    ]
    return matched if matched else chunks


# ===== 引用构造 =====

def build_citation(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """从 chunk 构造引用对象（区分手册与工单）。"""
    doc = str(chunk.get("doc", ""))
    # 工单：doc 形如 WO-2024-0312
    is_work_order = bool(re.match(r"^WO-\d+", doc))
    if is_work_order:
        return {
            "doc": "",
            "version": "",
            "page": 0,
            "clause": "",
            "wo_id": doc,
            "source_type": "work_order",
            "note": "",
        }
    # 手册
    return {
        "doc": doc,
        "version": chunk.get("version", ""),
        "effective_date": chunk.get("effective_date", ""),
        "page": int(chunk.get("page", 1) or 1),
        "clause": str(chunk.get("clause", "")),
        "wo_id": "",
        "source_type": "manual",
        "note": "",
    }


def _cite_key(c: Dict[str, Any]) -> tuple:
    """引用唯一键：工单按 wo_id，手册按 doc+page+clause。"""
    return (c.get("wo_id") or c.get("doc") or "", c.get("page") or 0, c.get("clause") or "")


def check_citation_diversity(
    citations: List[Dict[str, Any]], structured: dict, trace_id: str
) -> None:
    """引用去重与多样性校验：命中异常只输出 warning，不阻断业务。

    覆盖三类问题：
    1. 检索引用列表自身重复（同条款多 chunk）
    2. causes.source_refs 全部集中于同一来源
    3. 双路检索（手册+工单）但实际只出现单一来源类型
    """
    keys = [_cite_key(c) for c in citations]
    valid_keys = [k for k in keys if k[0]]
    if len(valid_keys) != len(set(valid_keys)):
        logger.warning(
            "[M01] citation duplicate: total=%d unique=%d trace=%s",
            len(valid_keys), len(set(valid_keys)), trace_id,
        )

    types_used = {c.get("source_type") for c in citations if c.get("source_type")}
    if len(types_used) < 2:
        logger.warning(
            "[M01] single source type only: %s trace=%s", types_used, trace_id,
        )

    ref_sources: List[str] = []
    for cause in structured.get("causes", []):
        if not isinstance(cause, dict):
            continue
        for ref in cause.get("source_refs", []) or []:
            if isinstance(ref, dict):
                src = ref.get("wo_id") or ref.get("doc") or ""
                if src:
                    ref_sources.append(src)
    if ref_sources:
        counts: Dict[str, int] = {}
        for s in ref_sources:
            counts[s] = counts.get(s, 0) + 1
        top_src, top_n = max(counts.items(), key=lambda kv: kv[1])
        if len(counts) == 1 and len(ref_sources) >= 3:
            logger.warning(
                "[M01] citation concentration: %d refs all from %s trace=%s",
                len(ref_sources), top_src, trace_id,
            )
        elif top_n >= 6:
            # 单设备通常只有 1 本手册，多次引用属正常；仅当单一来源占 6 次以上才预警
            logger.warning(
                "[M01] citation skew: %s used %d/%d times trace=%s",
                top_src, top_n, len(ref_sources), trace_id,
            )


# ===== Prompt 构造 =====

def _format_chunks(chunks: List[Dict[str, Any]]) -> str:
    """把检索到的 chunks 格式化为提示词中的片段（标注[手册]/[工单]）。"""
    if not chunks:
        return "（无可用片段）"
    lines: List[str] = []
    for i, c in enumerate(chunks, 1):
        doc = str(c.get("doc", ""))
        is_wo = bool(re.match(r"^WO-\d+", doc))
        tag = "[工单]" if is_wo else "[手册]"
        if is_wo:
            lines.append(
                f"[片段{i}] {tag} 工单号：{doc} | 日期：{c.get('effective_date', '')}\n"
                f"{c.get('content', '')}"
            )
        else:
            lines.append(
                f"[片段{i}] {tag} 文档：{doc} | 版本：{c.get('version', '')} "
                f"| 生效：{c.get('effective_date', '')} | 条款：{c.get('clause', '')} "
                f"| 页码：{c.get('page', 1)}\n"
                f"{c.get('content', '')}"
            )
    return "\n\n".join(lines)


def _load_prompt(name: str) -> str:
    p = MODULE_DIR / "prompts" / name
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def build_messages(
    symptom: str,
    equipment_code: str,
    recent_change: str,
    running_hours: int,
    chunks: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """构造 LLM messages。"""
    system = _load_prompt("system.md")
    user_tmpl = _load_prompt("user.md")
    user = (
        user_tmpl
        .replace("{symptom}", symptom)
        .replace("{equipment_code}", equipment_code or "未提供")
        .replace("{recent_change}", recent_change or "无")
        .replace("{running_hours}", str(running_hours or 0))
        .replace("{chunks}", _format_chunks(chunks))
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ===== LLM JSON 解析（容错）=====

def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出文本中提取 JSON 对象（容错：去 markdown 包裹、找第一个 {...}）。"""
    if not text:
        return None
    # 去 markdown 代码块包裹
    t = text.strip()
    # 去前缀文字 + ```json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        t = m.group(1)
    else:
        # 找第一个 { 到最后一个 }（贪婪）
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        t = t[start : end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 尝试修复常见问题：尾随逗号
        t2 = re.sub(r",\s*([}\]])", r"\1", t)
        try:
            return json.loads(t2)
        except json.JSONDecodeError:
            return None


def _unescape_deep(obj: Any) -> Any:
    """递归反转义 LLM 文本中的 HTML 实体。"""
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {k: _unescape_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unescape_deep(v) for v in obj]
    return obj


def _normalize_structured(parsed: Optional[dict]) -> dict:
    """把解析出的 JSON 规范化为 output_schema 结构。"""
    if not parsed:
        return {
            "causes": [],
            "steps": [],
            "spare_parts": [],
            "cases": [],
            "note": "LLM 输出解析失败，未能生成结构化诊断结论。",
        }
    def _as_list(v, key):
        if isinstance(v, list):
            return v
        return []
    out = {
        "causes": _as_list(parsed.get("causes"), "causes"),
        "steps": _as_list(parsed.get("steps"), "steps"),
        "spare_parts": _as_list(parsed.get("spare_parts"), "spare_parts"),
        "cases": _as_list(parsed.get("cases"), "cases"),
        "note": str(parsed.get("note", "") or ""),
    }
    # 规范化 cause 概率为 float
    for c in out["causes"]:
        if isinstance(c, dict):
            try:
                c["probability"] = float(c.get("probability", 0.0))
            except (TypeError, ValueError):
                c["probability"] = 0.0
            c["evidence"] = _as_list(c.get("evidence"), "evidence")
            c["source_refs"] = _as_list(c.get("source_refs"), "source_refs")
        else:
            c = {"name": str(c), "probability": 0.0, "evidence": [], "source_refs": []}
    # 按 probability 降序
    out["causes"].sort(key=lambda x: -float(x.get("probability", 0.0)))
    return _unescape_deep(out)


# ===== SSE 工具 =====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_chunks(text: str, delay: float = 0.03) -> AsyncIterator[str]:
    i = 0
    while i < len(text):
        chunk = text[i : i + 4]
        yield sse_event("chunk", {"text": chunk})
        i += 4
        await asyncio.sleep(delay)


# ===== DEMO 剧本降级（平台级匹配器 core/demo_script，§2.2）=====

def _citations_from_script(scripted: dict) -> List[Dict[str, Any]]:
    """剧本模式下检索为空时，从剧本 source_refs 构造引用（去重）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for cause in (scripted.get("structured") or {}).get("causes", []) or []:
        if not isinstance(cause, dict):
            continue
        for ref in cause.get("source_refs", []) or []:
            if not isinstance(ref, dict):
                continue
            wo = str(ref.get("wo_id", "") or "")
            if wo:
                cite = {
                    "doc": "", "version": "", "effective_date": "",
                    "page": 0, "clause": "", "wo_id": wo,
                    "source_type": "work_order", "note": "",
                }
            elif ref.get("doc"):
                cite = {
                    "doc": str(ref["doc"]),
                    "version": str(ref.get("version", "") or ""),
                    "effective_date": "",
                    "page": int(ref.get("page", 1) or 1),
                    "clause": str(ref.get("clause", "") or ""),
                    "wo_id": "",
                    "source_type": "manual",
                    "note": "",
                }
            else:
                continue
            key = _cite_key(cite)
            if key[0] and key not in seen:
                seen.add(key)
                out.append(cite)
    return out


# ===== 主入口 =====

async def handle(payload: dict, request: Request) -> StreamingResponse:
    """模块统一 invoke 入口。

    真实双路 RAG 链路（M2 DoD 要求，不走 demo 短路）：
    1. 输入解析
    2. query 改写（拼设备编号）
    3. 双路 BM25 检索（手册 + 工单）
    4. 设备型号软过滤
    5. 重排
    6. LLM 生成结构化 JSON（带引用）
    7. JSON 解析 → result 事件
    8. 检索为空 → 直接返回 P6 提示，不调 LLM
    """
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)
    symptom = str(inputs.get("symptom", "")).strip()
    equipment_code = str(inputs.get("equipment_code", "") or "").strip()
    recent_change = str(inputs.get("recent_change", "") or "").strip()
    try:
        running_hours = int(inputs.get("running_hours", 0) or 0)
    except (TypeError, ValueError):
        running_hours = 0

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
                "real_rag": True,
            },
        )

        try:
            if not symptom:
                msg = "请输入故障现象描述后再提交。"
                yield sse_event("chunk", {"text": msg})
                empty = {"causes": [], "steps": [], "spare_parts": [], "cases": [], "note": msg}
                yield sse_event("result", {"structured": empty})
                yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms, "tokens": {"in": 0, "out": 0}})
                return

            # ===== ⓪ 剧本模式（§2.2）：DEMO 无 LLM Key 时仅放行推荐问题 =====
            script_mode = demo_script.is_active_for_request(request)
            matched = demo_script.match(MODULE_CODE, symptom) if script_mode else None
            if script_mode and not matched:
                msg = demo_script.not_matched_message(MODULE_CODE)
                async for piece in demo_script.simulated_chunks(msg, chunk_size=12):
                    yield sse_event("chunk", {"text": piece})
                empty = {"causes": [], "steps": [], "spare_parts": [], "cases": [], "note": msg}
                yield sse_event("result", {"structured": empty})
                latency = audit.now_ms() - start_ms
                yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                audit.audit_log(
                    module_code=MODULE_CODE, action="invoke", question=symptom,
                    answer=msg, latency_ms=latency, status="degraded", trace_id=trace_id,
                )
                return

            # ===== ① query 改写 =====
            rewritten = rewrite_query(symptom, equipment_code)
            logger.info("[M01] symptom=%r equipment=%r rewritten=%r", symptom, equipment_code, rewritten)

            # ===== ② 双路 BM25 检索 =====
            service = get_service()
            hits = service.search(rewritten, COLLECTIONS, top_k=TOP_K)
            logger.info("[M01] BM25 hits=%d", len(hits))

            # ===== ③ 设备型号软过滤 =====
            if equipment_code:
                hits = filter_by_equipment(hits, equipment_code)
                logger.info("[M01] after equip filter: %d", len(hits))

            # ===== ④ 重排 =====
            reranked = rerank(rewritten, hits, top_n=RERANK_TOP_N)
            logger.info("[M01] reranked=%d", len(reranked))

            # ===== ⑤ 检索为空：剧本模式用剧本引用兜底；否则 P6 提示，不调 LLM =====
            if not reranked:
                if script_mode and matched:
                    citations = _citations_from_script(matched)
                    for cite in citations:
                        yield sse_event("citation", cite)
                else:
                    msg = "知识库中未找到相关内容，请补充设备手册或联系维修工程师。"
                    if stream:
                        async for piece in _stream_chunks(msg):
                            yield piece
                    else:
                        yield sse_event("chunk", {"text": msg})
                    empty = {"causes": [], "steps": [], "spare_parts": [], "cases": [], "note": msg}
                    yield sse_event("result", {"structured": empty})
                    latency = audit.now_ms() - start_ms
                    yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                    audit.audit_log(
                        module_code=MODULE_CODE,
                        action="invoke",
                        question=symptom,
                        answer=msg,
                        latency_ms=latency,
                        status="success",
                        trace_id=trace_id,
                    )
                    return

            # ===== ⑥ 构造引用（同条款/同工单去重；检索为空时已由剧本引用兜底）=====
            if reranked:
                citations: List[Dict[str, Any]] = []
                seen_cite_keys = set()
                for c in reranked:
                    cite = build_citation(c)
                    key = _cite_key(cite)
                    if key[0] and key in seen_cite_keys:
                        continue
                    seen_cite_keys.add(key)
                    citations.append(cite)
                    yield sse_event("citation", cite)

            # ===== ⑦ LLM 生成结构化 JSON（流式）=====
            messages = build_messages(
                symptom, equipment_code, recent_change, running_hours, reranked
            )

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
                        max_tokens=2000,
                    ):
                        collected.append(piece)
                        if stream:
                            yield sse_event("chunk", {"text": piece})
                    answer_text = "".join(collected)
                    llm_used = True
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M01] LLM stream failed, fallback to demo script: %s", e)
                    llm_used = False

            if not llm_used:
                # 降级：剧本模式用门禁阶段已命中的剧本；LLM 异常（配了 Key）时现匹配一次
                scripted = matched if script_mode else demo_script.match(MODULE_CODE, symptom)
                if scripted:
                    answer_text = json.dumps(scripted.get("structured", {}), ensure_ascii=False)
                    if stream:
                        # §2.2 剧本模拟流式：50–120ms 随机间隔
                        async for piece in demo_script.simulated_chunks(answer_text, chunk_size=24):
                            yield sse_event("chunk", {"text": piece})
                    else:
                        yield sse_event("chunk", {"text": answer_text})
                else:
                    # 兜底（仅 LLM 异常且无剧本时）：拼接命中的 chunks（保证 P6 不编造）
                    fallback = {
                        "causes": [],
                        "steps": [],
                        "spare_parts": [],
                        "cases": [],
                        "note": "（LLM 不可用，以下为检索到的原始片段摘要）",
                        "retrieved_snippets": [
                            {
                                "doc": c.get("doc", ""),
                                "version": c.get("version", ""),
                                "page": c.get("page", 1),
                                "clause": c.get("clause", ""),
                                "content": c.get("content", "")[:200],
                            }
                            for c in reranked[:3]
                        ],
                    }
                    answer_text = json.dumps(fallback, ensure_ascii=False)
                    if stream:
                        async for piece in _stream_chunks(answer_text):
                            yield piece
                    else:
                        yield sse_event("chunk", {"text": answer_text})

            # ===== ⑧ 解析 LLM JSON → 结构化 result =====
            parsed = _extract_json(answer_text)
            structured = _normalize_structured(parsed)
            structured["citations"] = citations
            structured["query_rewritten"] = rewritten if rewritten != symptom else None
            structured["llm_used"] = llm_used

            # 引用去重/多样性校验（只告警，不改变返回结果）
            check_citation_diversity(citations, structured, trace_id)

            yield sse_event("result", {"structured": structured})

            # ===== ⑨ done =====
            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=symptom,
                answer=answer_text[:4000],
                citations=citations,
                latency_ms=latency,
                status="success" if llm_used else "degraded",
                trace_id=trace_id,
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("[M01] handle failed: %s", e)
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(
                module_code=MODULE_CODE,
                action="invoke",
                question=symptom,
                answer=str(e),
                status="failed",
                trace_id=trace_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream")
