"""M03 质量异常分析与 8D 报告模块业务逻辑（§9.3）。

真实链路（M2 DoD，不走 demo 短路）：
1. 输入：batch_no / line / defect_desc（必填）/ metrics
2. RAG：BM25 检索历史质量案例库（quality_case），重排取 top N
3. LLM：基于真实案例生成结构化 JSON（problem_summary / five_why / fishbone /
   similar_cases / report_8d[d1..d8]）
4. 引用防编造：similar_cases 的 case_id 必须命中真实检索结果，未命中丢弃
5. 导出：core/template.render_to_docx 渲染 D1–D8 Word（含 5Why 表、鱼骨图 6M 表、
   相似案例附录），写入 FileStore 的 exports/，注册到 /api/files 下载
6. P6：案例库无相似记录时明确提示，不生成报告、不调用 LLM

SSE：meta → citation* → chunk*（流式 JSON 文本）→ result（结构化含 export_url）→ done
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import yaml
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.adapters.factory import get_file_store
from app.api.files import register_generated_file
from app.config import settings
from app.core import audit, demo_script
from app.core.llm_gateway import get_gateway
from app.core.rag.reranker import rerank
from app.core.rag.retriever import get_service
from app.core.template import render_to_docx, render_to_pdf

logger = logging.getLogger(__name__)

MODULE_CODE = "m03_quality_8d"
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATE_8D = MODULE_DIR / "seed" / "template_8d.yaml"

COLLECTION = "quality_case"
TOP_K = 8
RERANK_TOP_N = 6
# BM25 相关度门槛（低于此分视为无相似案例，触发 P6；按实测标定：
# 真实相关 query top_score≈71-159，完全无关 query≈12，取中间值 25）
MIN_SCORE = 25.0

FISHBONE_CATEGORIES = ["人", "机", "料", "法", "环", "测"]
D8_KEYS = [
    ("d1_team", "D1"), ("d2_problem", "D2"), ("d3_containment", "D3"),
    ("d4_root_cause", "D4"), ("d5_corrective", "D5"), ("d6_verify", "D6"),
    ("d7_prevent", "D7"), ("d8_recognize", "D8"),
]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_CASE_ID_RE = re.compile(r"QC-\d{4}-\d{4}")

# 头部字段兜底抽取（表单未填时，从异常描述/指标文本中正则提取）
_BATCH_RE = re.compile(r"\b([A-Z]{2,}-[A-Z0-9]+(?:-[A-Z0-9]+)*)")
_RATE_RES = (
    re.compile(r"(?:不良率|缺陷率|不合格率)[：:\s]*([0-9]+(?:\.[0-9]+)?%?)"),
    re.compile(r"([0-9]+(?:\.[0-9]+)?%)(?:\s*的?不良|\s*缺陷|\s*不合格)"),
)
_LINE_RE = re.compile(r"(\d+\s*号?线|[A-Za-z]\s*线|[一二三四五六七八九十]+\s*号?线|[\u4e00-\u9fa5A-Za-z0-9]{1,8}?车间)")
# markdown 表格残留（模型偶尔把 | --- | 分隔行塞进因素文本）
_MD_SEP_RE = re.compile(r"^[\s|:：\-—.]+$")


def extract_header_fields(inputs: dict) -> Dict[str, str]:
    """表单字段优先；缺失时从 defect_desc/metrics 文本抽取批次号、产线、不良率。

    抽不到返回空串（导出时统一显示「—」，禁止出现"未提供"）。
    """
    desc = str(inputs.get("defect_desc") or "")
    metrics_text = str(inputs.get("metrics") or "")
    text = f"{desc} {metrics_text}"

    batch = str(inputs.get("batch_no") or "").strip()
    if not batch:
        m = _BATCH_RE.search(text)
        if m:
            batch = m.group(1)

    line = str(inputs.get("line") or "").strip()
    if not line:
        m = _LINE_RE.search(text)
        if m:
            line = re.sub(r"\s+", "", m.group(1))

    rate = metrics_text.strip()
    if not rate:
        for rx in _RATE_RES:
            m = rx.search(text)
            if m:
                v = m.group(1)
                rate = f"不良率 {v}" if not v.endswith("%") else f"不良率 {v}"
                break

    return {"batch_no": batch, "line": line, "metrics": rate}


# ===== 资源加载 =====

def load_manifest() -> dict:
    return yaml.safe_load((MODULE_DIR / "manifest.yaml").read_text(encoding="utf-8"))


def _load_prompt(name: str) -> str:
    return (MODULE_DIR / "prompts" / name).read_text(encoding="utf-8")


# 剧本加载/匹配统一走平台级 core/demo_script（§2.2）


# ===== 检索与引用 =====

def extract_case_id(chunk: Dict[str, Any]) -> str:
    m = _CASE_ID_RE.search(str(chunk.get("doc", "")))
    return m.group(0) if m else ""


def build_query(batch_no: str, line: str, defect_desc: str, metrics: str) -> str:
    parts = [defect_desc]
    for v in (line, batch_no, metrics):
        if v and v not in defect_desc:
            parts.append(v)
    return " ".join(p for p in parts if p)


def format_cases(chunks: List[Dict[str, Any]]) -> str:
    """把案例 chunks 格式化为 prompt 片段（每案例一块，编号醒目）。"""
    lines: List[str] = []
    for i, c in enumerate(chunks, 1):
        lines.append(
            f"[案例{i}] 编号：{extract_case_id(c)} | 名称：{c.get('doc', '')} "
            f"| 日期：{c.get('effective_date', '')}\n{c.get('content', '')}"
        )
    return "\n\n".join(lines)


def build_citation(chunk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "doc": str(chunk.get("doc", "")),
        "case_id": extract_case_id(chunk),
        "version": chunk.get("version", ""),
        "effective_date": chunk.get("effective_date", ""),
        "page": int(chunk.get("page", 1) or 1),
        "clause": "历史质量案例",
        "wo_id": "",
        "source_type": "quality_case",
        "note": "",
    }


# ===== Prompt =====

def build_messages(inputs: dict, chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    user = (
        _load_prompt("user.md")
        .replace("{batch_no}", inputs.get("batch_no") or "未提供")
        .replace("{line}", inputs.get("line") or "未提供")
        .replace("{defect_desc}", inputs.get("defect_desc") or "")
        .replace("{metrics}", inputs.get("metrics") or "未提供")
        .replace("{cases}", format_cases(chunks))
    )
    return [
        {"role": "system", "content": _load_prompt("system.md")},
        {"role": "user", "content": user},
    ]


# ===== JSON 解析与规范化 =====

def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        t = m.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        t = t[start : end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        t2 = re.sub(r",\s*([}\]])", r"\1", t)
        try:
            return json.loads(t2)
        except json.JSONDecodeError:
            return None


def _unescape_deep(obj: Any) -> Any:
    """递归反转义 LLM 文本中的 HTML 实体（结构化出口统一处理，界面/导出双干净）。"""
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {k: _unescape_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unescape_deep(v) for v in obj]
    return obj


def _clean_factor_text(raw: Any) -> str:
    """清洗因素文本：去 markdown 表格竖线/分隔行，折叠空白。"""
    s = str(raw or "").replace("|", " ")
    s = re.sub(r"-{3,}", "—", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 纯分隔符行（---、|:--: 等）或清洗后为空 → 丢弃
    if not s or _MD_SEP_RE.match(s):
        return ""
    return s


def _norm_factor(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, str):
        factor = _clean_factor_text(item)
        return {"factor": factor, "is_primary": False} if factor else None
    if isinstance(item, dict) and item.get("factor"):
        factor = _clean_factor_text(item["factor"])
        if not factor:
            return None
        return {"factor": factor, "is_primary": bool(item.get("is_primary", False))}
    return None


def normalize_structured(parsed: Optional[dict], valid_case_ids: set) -> dict:
    """规范化为 output_schema；similar_cases 只保留真实命中的案例编号。"""
    empty_d8 = {k: "" for k, _ in D8_KEYS}
    if not parsed:
        return {
            "problem_summary": "",
            "five_why": [],
            "fishbone": {c: [] for c in FISHBONE_CATEGORIES},
            "similar_cases": [],
            "report_8d": empty_d8,
            "note": "LLM 输出解析失败，未能生成结构化分析。",
        }

    # 5Why
    five_why: List[Dict[str, Any]] = []
    raw_fw = parsed.get("five_why") if isinstance(parsed.get("five_why"), list) else []
    for i, item in enumerate(raw_fw[:5], 1):
        if isinstance(item, dict):
            five_why.append({
                "level": int(item.get("level", i) or i),
                "question": str(item.get("question", "")),
                "answer": str(item.get("answer", "")),
            })

    # 鱼骨图：固定六维度，每类固定 3 行（共 18 行，不足补空占位，超出取前 3）
    raw_fb = parsed.get("fishbone") if isinstance(parsed.get("fishbone"), dict) else {}
    fishbone: Dict[str, List[Dict[str, Any]]] = {}
    for cat in FISHBONE_CATEGORIES:
        factors = []
        for it in raw_fb.get(cat, []) if isinstance(raw_fb.get(cat), list) else []:
            f = _norm_factor(it)
            if f:
                factors.append(f)
            if len(factors) >= 3:
                break
        while len(factors) < 3:
            factors.append({"factor": "", "is_primary": False})
        fishbone[cat] = factors

    # 相似案例：编号必须在真实检索集合内（防编造）
    similar: List[Dict[str, Any]] = []
    raw_cases = parsed.get("similar_cases") if isinstance(parsed.get("similar_cases"), list) else []
    seen = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("case_id", "")).strip()
        m = _CASE_ID_RE.search(cid)
        cid = m.group(0) if m else ""
        if cid and cid in valid_case_ids and cid not in seen:
            seen.add(cid)
            similar.append({
                "case_id": cid,
                "title": str(item.get("title", "")),
                "similarity": str(item.get("similarity", "")),
                "lesson": str(item.get("lesson", "")),
            })

    # 8D 八段
    raw_r = parsed.get("report_8d") if isinstance(parsed.get("report_8d"), dict) else {}
    report_8d = {k: str(raw_r.get(k, "") or "") for k, _ in D8_KEYS}

    return _unescape_deep({
        "problem_summary": str(parsed.get("problem_summary", "") or ""),
        "five_why": five_why,
        "fishbone": fishbone,
        "similar_cases": similar,
        "report_8d": report_8d,
        "note": "",
    })


# ===== 降级：无 LLM 时基于检索片段构造非编造结构 =====

def fallback_structured(inputs: dict, chunks: List[Dict[str, Any]]) -> dict:
    cases = []
    for c in chunks[:4]:
        cid = extract_case_id(c)
        if not cid:
            continue
        # 从案例正文摘一段"根本原因"附近内容作为借鉴（忠实原文，不编造）
        content = str(c.get("content", ""))
        excerpt = ""
        m = re.search(r"根本原因[：:](.{20,400}?)(?:永久纠正措施|预防再发生|$)", content, re.DOTALL)
        if m:
            excerpt = re.sub(r"\s+", "", m.group(1))[:180]
        cases.append({
            "case_id": cid,
            "title": str(c.get("doc", "")),
            "similarity": "（LLM 不可用，按关键词相关度检索）",
            "lesson": excerpt,
        })
    wait = "待质量工程师补充（当前 LLM 服务不可用，以下仅提供检索到的真实历史案例参考）"
    return {
        "problem_summary": str(inputs.get("defect_desc", "")),
        "five_why": [],
        "fishbone": {c: [] for c in FISHBONE_CATEGORIES},
        "similar_cases": cases,
        "report_8d": {k: wait for k, _ in D8_KEYS},
        "note": "LLM 不可用，已降级为案例检索结果；5Why/鱼骨图/8D 措施请人工补充。",
    }


# ===== Word 导出 =====

def _split_actions(text: str) -> List[str]:
    """把 LLM 的分号分隔措施串拆成条目。"""
    if not text:
        return []
    parts = re.split(r"[；;\n]+", text)
    out = []
    for p in parts:
        p = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩\d.、）)]+\s*", "", p).strip()
        if p:
            out.append(p)
    return out


def build_doc_data(inputs: dict, structured: dict, report_no: str) -> dict:
    """把结构化结果转为 core/template 的通用文档模型。"""
    r = structured.get("report_8d", {})
    # 头部字段：表单优先，缺失时从异常描述文本兜底抽取；仍无则显示「—」
    header = extract_header_fields(inputs)
    dash = lambda v: v if v else "—"  # noqa: E731

    # 鱼骨图：六类 × 每类 3 行 = 固定 18 行（降级数据未经 normalize 时在此兜底补齐）
    fishbone_rows = []
    for cat in FISHBONE_CATEGORIES:
        cat_factors = [
            f for f in structured.get("fishbone", {}).get(cat, [])
            if _clean_factor_text(f.get("factor", ""))
        ][:3]
        for f in cat_factors:
            fishbone_rows.append([
                cat,
                _clean_factor_text(f.get("factor", "")),
                "★ 主因" if f.get("is_primary") else "",
            ])
        for _ in range(3 - len(cat_factors)):
            fishbone_rows.append([cat, "—", ""])

    five_why_rows = [
        [fw.get("level", i), fw.get("question", ""), fw.get("answer", "")]
        for i, fw in enumerate(structured.get("five_why", []), 1)
    ]

    case_rows = [
        [c.get("case_id", ""), c.get("title", ""), c.get("similarity", ""), c.get("lesson", "")]
        for c in structured.get("similar_cases", [])
    ]

    sections = {
        "D1": {"paragraphs": [r.get("d1_team", "")]},
        "D2": {"paragraphs": [x for x in (structured.get("problem_summary", ""), r.get("d2_problem", "")) if x]},
        "D3": {"bullets": _split_actions(r.get("d3_containment", "")) or [r.get("d3_containment", "")]},
        "D4": {
            "paragraphs": [x for x in (r.get("d4_root_cause", ""),) if x],
            "tables": [
                {"title": "表 4-1 5Why 根因追问", "headers": ["层级", "追问（为什么）", "分析与答案"], "rows": five_why_rows},
                {"title": "表 4-2 鱼骨图分析（人/机/料/法/环/测）", "headers": ["类别", "潜在因素", "判定"], "rows": fishbone_rows},
            ],
        },
        "D5": {"bullets": _split_actions(r.get("d5_corrective", "")) or [r.get("d5_corrective", "")]},
        "D6": {"paragraphs": [r.get("d6_verify", "")]},
        "D7": {"bullets": _split_actions(r.get("d7_prevent", "")) or [r.get("d7_prevent", "")]},
        "D8": {"paragraphs": [r.get("d8_recognize", "")]},
    }

    meta = [
        ("报告编号", report_no),
        ("批次号", dash(header["batch_no"])),
        ("产线/车间", dash(header["line"])),
        ("异常现象", inputs.get("defect_desc") or "—"),
        ("关键指标", dash(header["metrics"])),
        ("报告日期", datetime.now().strftime("%Y-%m-%d")),
        ("编制说明", "AI 基于历史质量案例辅助生成，经质量工程师审核后签发"),
    ]

    appendix = []
    if case_rows:
        appendix.append({
            "title": "附录：检索引用的历史质量案例",
            "headers": ["案例编号", "案例名称", "相似点", "经验借鉴"],
            "rows": case_rows,
        })

    return {"meta": meta, "sections": sections, "appendix": appendix}


def export_report(inputs: dict, structured: dict, report_no: str, fmt: str = "docx") -> str:
    """渲染 Word/PDF → FileStore exports/ → 注册下载，返回 export_url。"""
    import tempfile

    doc_data = build_doc_data(inputs, structured, report_no)
    store = get_file_store()
    if fmt == "pdf":
        filename = f"8D-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}.pdf"
        storage_path = f"exports/{filename}"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            render_to_pdf(str(TEMPLATE_8D), doc_data, tmp_path)
            store.save(storage_path, Path(tmp_path).read_bytes())
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        file_id = register_generated_file(filename, storage_path, "application/pdf")
        return f"/api/files/{file_id}"

    filename = f"8D-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}.docx"
    storage_path = f"exports/{filename}"
    # 先渲染到临时文件再存入 FileStore（FileStore 只接收 bytes）
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        render_to_docx(str(TEMPLATE_8D), doc_data, tmp_path)
        store.save(storage_path, Path(tmp_path).read_bytes())
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    file_id = register_generated_file(filename, storage_path, DOCX_MIME)
    return f"/api/files/{file_id}"


# ===== SSE =====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_text(text: str, delay: float = 0.02) -> AsyncIterator[str]:
    i = 0
    while i < len(text):
        chunk = text[i : i + 6]
        yield sse_event("chunk", {"text": chunk})
        i += 6
        await asyncio.sleep(delay)


# ===== 主入口 =====

async def handle(payload: dict, request: Request) -> StreamingResponse:
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)

    trace_id = audit.new_trace_id()
    start_ms = audit.now_ms()
    report_no = f"8D-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    defect_desc = str(inputs.get("defect_desc", "")).strip()
    line = str(inputs.get("line", "") or "").strip()
    batch_no = str(inputs.get("batch_no", "") or "").strip()
    metrics = str(inputs.get("metrics", "") or "").strip()
    clean_inputs = {"defect_desc": defect_desc, "line": line, "batch_no": batch_no, "metrics": metrics}

    async def gen() -> AsyncIterator[str]:
        yield sse_event("meta", {
            "trace_id": trace_id,
            "module": MODULE_CODE,
            "demo_mode": settings.DEMO_MODE,
            "real_rag": True,
        })

        try:
            if not defect_desc:
                msg = "请输入质量异常现象描述后再提交。"
                async for piece in _stream_text(msg):
                    yield piece
                yield sse_event("result", {"structured": {"note": msg, "export_url": ""}})
                yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms, "tokens": {"in": 0, "out": 0}})
                return

            # ===== ⓪ 剧本模式（§2.2）：DEMO 无 Key 时仅放行推荐问题 =====
            script_mode = demo_script.is_active_for_request(request)
            matched = demo_script.match(MODULE_CODE, defect_desc) if script_mode else None
            if script_mode and not matched:
                msg = demo_script.not_matched_message(MODULE_CODE)
                async for piece in demo_script.simulated_chunks(msg, chunk_size=12):
                    yield sse_event("chunk", {"text": piece})
                structured = {
                    "problem_summary": defect_desc,
                    "five_why": [],
                    "fishbone": {c: [] for c in FISHBONE_CATEGORIES},
                    "similar_cases": [],
                    "report_8d": {k: "" for k, _ in D8_KEYS},
                    "export_url": "",
                    "note": msg,
                }
                yield sse_event("result", {"structured": structured})
                latency = audit.now_ms() - start_ms
                yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                audit.audit_log(module_code=MODULE_CODE, action="invoke",
                                question=defect_desc, answer=msg,
                                latency_ms=latency, status="degraded", trace_id=trace_id)
                return

            # ===== ① 检索相似历史案例 =====
            query = build_query(batch_no, line, defect_desc, metrics)
            service = get_service()
            hits = service.search(query, [COLLECTION], top_k=TOP_K)
            reranked = rerank(query, hits, top_n=RERANK_TOP_N)
            top_score = float(reranked[0].get("score", 0)) if reranked else 0.0
            logger.info("[M03] hits=%d reranked=%d top_score=%.3f", len(hits), len(reranked), top_score)

            # ===== ② P6：无相似案例 → 明确提示，不生成报告（剧本模式命中时放行）=====
            if (not reranked or top_score < MIN_SCORE) and not (script_mode and matched):
                msg = ("历史质量案例库中未检索到与该异常足够相似的案例，"
                       "为避免凭空生成误导性结论，暂不自动生成 8D 报告。"
                       "请补充缺陷现象关键词，或由质量工程师人工立案分析。")
                if stream:
                    async for piece in _stream_text(msg):
                        yield piece
                else:
                    yield sse_event("chunk", {"text": msg})
                structured = {
                    "problem_summary": defect_desc,
                    "five_why": [],
                    "fishbone": {c: [] for c in FISHBONE_CATEGORIES},
                    "similar_cases": [],
                    "report_8d": {k: "" for k, _ in D8_KEYS},
                    "export_url": "",
                    "note": msg,
                }
                yield sse_event("result", {"structured": structured})
                latency = audit.now_ms() - start_ms
                yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                audit.audit_log(module_code=MODULE_CODE, action="invoke",
                                question=defect_desc, answer=msg,
                                latency_ms=latency, status="success", trace_id=trace_id)
                return

            # ===== ③ 引用推送（真实案例）=====
            citations = [build_citation(c) for c in reranked]
            valid_case_ids = {c["case_id"] for c in citations if c["case_id"]}
            for cite in citations:
                yield sse_event("citation", cite)

            # ===== ④ LLM 生成结构化 JSON =====
            answer_text = ""
            llm_used = False
            if settings.llm_ready:
                try:
                    gateway = get_gateway()
                    collected: List[str] = []
                    for piece in gateway.chat_stream(
                        build_messages(clean_inputs, reranked),
                        module_code=MODULE_CODE,
                        temperature=0.3,
                        max_tokens=4000,
                    ):
                        collected.append(piece)
                        if stream:
                            yield sse_event("chunk", {"text": piece})
                    answer_text = "".join(collected)
                    llm_used = True
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M03] LLM failed, fallback: %s", e)
                    llm_used = False

            # ===== ⑤ 解析 / 降级（剧本统一走平台匹配器 core/demo_script）=====
            if llm_used:
                structured = normalize_structured(extract_json(answer_text), valid_case_ids)
                # 解析彻底失败时降级，保证仍能导出报告
                if not structured["five_why"] and not structured["report_8d"].get("d4_root_cause"):
                    scripted = demo_script.match(MODULE_CODE, defect_desc)
                    if scripted:
                        structured = normalize_structured(scripted["structured"], valid_case_ids)
                        structured["note"] = "LLM 输出解析失败，已使用预置剧本降级生成。"
                    else:
                        structured = fallback_structured(clean_inputs, reranked)
            else:
                scripted = matched if script_mode else demo_script.match(MODULE_CODE, defect_desc)
                if scripted:
                    answer_text = json.dumps(scripted["structured"], ensure_ascii=False)
                    if stream:
                        # §2.2 剧本模拟流式：50–120ms 随机间隔
                        async for piece in demo_script.simulated_chunks(answer_text, chunk_size=24):
                            yield sse_event("chunk", {"text": piece})
                    else:
                        yield sse_event("chunk", {"text": answer_text})
                    structured = normalize_structured(scripted["structured"], valid_case_ids)
                else:
                    structured = fallback_structured(clean_inputs, reranked)
                    payload_text = json.dumps(structured, ensure_ascii=False)
                    if stream:
                        async for piece in _stream_text(payload_text):
                            yield piece
                    else:
                        yield sse_event("chunk", {"text": payload_text})

            structured["citations"] = citations
            structured["llm_used"] = llm_used
            structured["report_no"] = report_no

            # ===== ⑥ Word / PDF 导出（真实文件）=====
            try:
                export_url = export_report(clean_inputs, structured, report_no)
                structured["export_url"] = export_url
                logger.info("[M03] report exported: %s", export_url)
            except Exception as e:  # noqa: BLE001
                logger.exception("[M03] docx export failed: %s", e)
                structured["export_url"] = ""
                structured["note"] = (structured.get("note") or "") + f"（Word 导出失败：{e}）"
            try:
                structured["export_url_pdf"] = export_report(clean_inputs, structured, report_no, fmt="pdf")
            except Exception as e:  # noqa: BLE001
                logger.exception("[M03] pdf export failed: %s", e)
                structured["export_url_pdf"] = ""

            yield sse_event("result", {"structured": structured})

            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE, action="invoke", question=defect_desc,
                answer=answer_text[:4000] or "(degraded)",
                citations=citations, latency_ms=latency,
                status="success" if llm_used else "degraded", trace_id=trace_id,
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("[M03] handle failed: %s", e)
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(module_code=MODULE_CODE, action="invoke",
                            question=defect_desc, answer=str(e),
                            latency_ms=audit.now_ms() - start_ms,
                            status="failed", trace_id=trace_id)

    return StreamingResponse(gen(), media_type="text/event-stream")
