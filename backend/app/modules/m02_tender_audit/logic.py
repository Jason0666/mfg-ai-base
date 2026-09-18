"""M02 招标文件智能审核业务逻辑（§9.2）。

真实链路：
1. 取文件：上传的 file_id 走 FileStore；为空时使用内置 66 页模拟招标文件
2. file_parse：PyMuPDF 按页解析（保留真实页码），Word 兼容
3. 招标文件特征检测（P6：非招标文件明确拒绝，不编造结论）
4. 规则引擎：资格/技术/盖章/时间条款发现（确定性骨架，覆盖埋点）→ 企业档案数值比对
5. RAG：废标审核要点库检索，推送引用
6. LLM：在候选条款片段上补充规则遗漏项 + 撰写审核结论（判定结果以规则引擎为准）
7. 风险分级 → 五类结构化输出 → 导出 Word 审核报告

SSE：meta → citation* → chunk*（结论文本）→ result → done
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.adapters.factory import get_file_store
from app.api import files as files_api
from app.config import settings
from app.core import audit
from app.core.document_parser import parse_document
from app.core.llm_gateway import get_gateway
from app.core.rag.reranker import rerank
from app.core.rag.retriever import get_service
from app.core.template import render_to_docx, render_to_pdf
from seed.build_tender_pdf import PDF_PATH, ensure_sample

from . import rules

logger = logging.getLogger(__name__)

MODULE_CODE = "m02_tender_audit"
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATE_AUDIT = MODULE_DIR / "seed" / "template_audit.yaml"
PROFILE_PATH = MODULE_DIR / "seed" / "company_profile.json"

COLLECTION = "m02_tender_audit"
TOP_K = 6
RERANK_TOP_N = 4
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 候选条款页预筛关键词（控制给 LLM 的篇幅）
CLAUSE_KEYWORDS = [
    "资格", "资质", "注册", "资本", "审计", "财务", "认证", "ISO", "业绩", "信誉",
    "处理能力", "产能", "精度", "OEE", "MTBF", "质保", "参数", "偏离",
    "盖章", "签字", "公章", "密封", "保证金", "截止", "开标", "递交", "授权",
    "响应时间", "有效期",
]
MAX_CLAUSE_CHARS = 12000

_DEVIATION_TEXT = {"negative": "负偏离", "positive": "正偏离（优于要求）",
                   "none": "无偏离", "unknown": "档案未提供，需人工确认"}
_LEVEL_TEXT = {"high": "高", "medium": "中", "low": "低"}


# ===== 资源 =====

def _load_prompt(name: str) -> str:
    return (MODULE_DIR / "prompts" / name).read_text(encoding="utf-8")


def load_profile(raw_text: str) -> tuple[dict, str]:
    """解析企业档案：空→内置 JSON；JSON→直接用；其他文本→内置档案+附加备注。"""
    builtin = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return builtin, ""
    try:
        return json.loads(raw_text), ""
    except json.JSONDecodeError:
        return builtin, raw_text


def get_file_bytes(file_id: str) -> tuple[str, bytes]:
    """按 file_id 从 FileStore 取文件；file_id 为空时返回内置样本。"""
    if not file_id:
        ensure_sample()
        return PDF_PATH.name, PDF_PATH.read_bytes()
    meta = files_api._FILES.get(file_id)
    if meta is None:
        raise FileNotFoundError(f"file_id 不存在或已过期：{file_id}（请重新上传）")
    store = get_file_store()
    return meta["filename"], store.load(meta["storage_path"])


# ===== RAG 引用 =====

def build_citation(chunk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "doc": str(chunk.get("doc", "")),
        "case_id": "",
        "version": chunk.get("version", ""),
        "effective_date": chunk.get("effective_date", ""),
        "page": int(chunk.get("page", 1) or 1),
        "clause": "废标审核要点",
        "wo_id": "",
        "source_type": "tender_note",
        "note": str(chunk.get("content", ""))[:120],
    }


# ===== LLM 候选片段与 JSON =====

def pick_clause_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    picked = []
    total = 0
    for pg in pages:
        text = pg.get("text", "")
        if any(kw in text for kw in CLAUSE_KEYWORDS):
            seg = text[:1500]
            picked.append({"page": pg["page"], "clause": pg.get("clause", ""), "text": seg})
            total += len(seg)
            if total >= MAX_CLAUSE_CHARS:
                break
    return picked


def format_clauses(picked: List[Dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[第{p['page']}页｜{p.get('clause','')}] {p['text']}" for p in picked
    )


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


# ===== LLM 补充合并（规则骨架优先，LLM 只做可验证补集）=====

def merge_llm_extras(parsed: Optional[dict], pages: List[Dict[str, Any]],
                     profile: dict, base_qual: list, base_tech: list,
                     base_seals: list, base_timelines: list):
    """返回 (qual_req 增补可比对项, tech 增补, seals 增补, timelines 增补, 页码白名单集合)。"""
    extra_qual: List[Dict[str, Any]] = []
    extra_tech: List[Dict[str, Any]] = []
    extra_seals: List[Dict[str, Any]] = []
    extra_times: List[Dict[str, Any]] = []
    valid_pages = {p["page"] for p in pages}
    page_text = {p["page"]: re.sub(r"\s+", "", p.get("text", "")) for p in pages}
    page_clause = {p["page"]: (p.get("clause") or "招标文件相关条款") for p in pages}

    def _clause_of(page: int) -> str:
        return page_clause.get(page, "招标文件相关条款")

    def _num_in_page(page: int, value: float) -> bool:
        flat = page_text.get(page, "")
        forms = {str(value)}
        if float(value).is_integer():
            forms.add(str(int(value)))
        return any(f in flat for f in forms)

    def _date_in_page(page: int, dt: datetime) -> bool:
        flat = page_text.get(page, "")
        return (f"{dt.year}年{dt.month}月{dt.day}日" in flat
                or dt.strftime("%Y-%m-%d") in flat
                or dt.strftime("%Y/%m/%d") in flat)

    if not parsed:
        return extra_qual, extra_tech, extra_seals, extra_times

    known_qual_keys = {"registered_capital", "similar_count"} | set(rules.QUAL_KEYWORDS)
    exist_q = {x["key"] for x in base_qual}
    for it in parsed.get("qualifications", []) or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key", ""))
        page = int(it.get("page", 0) or 0)
        if key in known_qual_keys and key not in exist_q and page in valid_pages:
            extra_qual.append({
                "key": key, "page": page,
                "clause": _clause_of(page),
                "requirement": str(it.get("requirement", ""))[:200],
                "required_value": it.get("required_value"),
                "unit": str(it.get("unit", "")),
            })

    profile_items = {p.get("item") for p in profile.get("tech_params", [])}
    exist_t = {x["item"] for x in base_tech}
    for it in parsed.get("tech", []) or []:
        if not isinstance(it, dict):
            continue
        item = str(it.get("item", ""))
        page = int(it.get("page", 0) or 0)
        try:
            value = float(it.get("value"))
        except (TypeError, ValueError):
            continue
        if (item in profile_items and item not in exist_t
                and page in valid_pages and _num_in_page(page, value)):
            bw = str(it.get("bound_word", "不低于"))
            extra_tech.append({
                "item": item, "page": page, "clause": _clause_of(page),
                "value": value, "unit": rules.normalize_unit(str(it.get("unit", ""))),
                "lower_bound": bw in ("不低于", "不少于", "≥", ">=", ">"),
                "bound_word": bw, "material": bool(it.get("material", False)),
                "raw": "",
            })

    exist_s = {x["key"] for x in base_seals}
    for it in parsed.get("seals", []) or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key", ""))
        page = int(it.get("page", 0) or 0)
        if key and key not in exist_s and page in valid_pages and it.get("desc"):
            extra_seals.append({"key": key, "page": page,
                                "clause": _clause_of(page), "desc": str(it.get("desc"))[:200]})

    exist_tm = {x["key"] for x in base_timelines}
    # 同一截止时间可能在多页复述（邀请页 + 格式/保证金页），按截止时间去重
    seen_dl = {x["_dt"].strftime("%Y-%m-%d %H:%M")
               for x in base_timelines if x.get("_dt") is not None}
    for it in parsed.get("timelines", []) or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key", ""))
        dl = rules.parse_cn_date(str(it.get("deadline", "")))
        page = int(it.get("page", 0) or 0)
        dl_str = dl.strftime("%Y-%m-%d %H:%M") if dl else ""
        if (key and key not in exist_tm and dl and dl_str not in seen_dl
                and page in valid_pages
                and _date_in_page(page, dl)):
            extra_times.append({"key": key, "event": str(it.get("event", key)),
                                "deadline": dl_str,
                                "_dt": dl, "page": page})
            seen_dl.add(dl_str)
    return extra_qual, extra_tech, extra_seals, extra_times


# ===== 结论降级文本 =====

def build_summary(risks: list, qual_rows: list, tech_rows: list) -> str:
    high = [r for r in risks if r["level"] == "high"]
    med = [r for r in risks if r["level"] == "medium"]
    neg = [t for t in tech_rows if t["deviation"] == "negative"]
    q_bad = [q for q in qual_rows if not q["match"]]
    verdict = "建议放弃投标" if high else ("整改后可投" if med else "可投，按清单例行核查")
    lines = [f"审核结论：{verdict}。"]
    if high:
        lines.append("高风险事项 %d 项：%s。" % (
            len(high), "；".join(r["reason"].split("（")[0][:40] for r in high)))
    if q_bad:
        lines.append("资格核查 %d 项不满足/缺失：%s。" % (
            len(q_bad), "、".join(q["requirement"][:18] for q in q_bad)))
    if neg:
        lines.append("技术负偏离 %d 项：%s（实质性条款不允许负偏离）。" % (
            len(neg), "、".join(f"{n['item']}（{n['required']}→{n['ours']}）" for n in neg)))
    if med:
        lines.append("中风险事项 %d 项（盖章/时间等），须按清单逐项落实并双人复核。" % len(med))
    lines.append("详见后文各清单与页码定位。")
    return "".join(lines)


# ===== Word 导出 =====

def build_doc_data(*, filename: str, page_count: int, profile: dict,
                   risks: list, qual_rows: list, tech_rows: list,
                   seals: list, timelines: list, summary: str,
                   citations: list, report_no: str) -> dict:
    risk_rows = [[
        _LEVEL_TEXT.get(r["level"], r["level"]),
        r.get("clause", ""),
        str(r.get("page", "")),
        r.get("reason", ""),
        {"missing": "缺失", "mismatch": "不满足", "negative": "负偏离",
         "to_check": "待核查", "pending": "待办理"}.get(r.get("our_status", ""), r.get("our_status", "")),
        r.get("suggestion", ""),
    ] for r in risks]

    qual_rows_doc = [[
        q["requirement"], q["ours"], "是" if q["match"] else "否", str(q.get("page", ""))
    ] for q in qual_rows]

    tech_rows_doc = [[
        t["item"], t["required"], t["ours"],
        _DEVIATION_TEXT.get(t["deviation"], t["deviation"]),
        "是" if t.get("material") else "否", str(t.get("page", "")),
    ] for t in tech_rows]

    seal_rows = [[str(s.get("page", "")), s.get("clause", ""), s.get("desc", "")] for s in seals]
    time_rows = [[
        t["event"], t["deadline"], str(t.get("days_left", "")),
        _LEVEL_TEXT.get(t.get("level", ""), ""), str(t.get("page", "")),
    ] for t in timelines]

    n_high = sum(1 for r in risks if r["level"] == "high")
    n_med = sum(1 for r in risks if r["level"] == "medium")
    meta = [
        ("报告编号", report_no),
        ("招标文件", filename),
        ("文件页数", f"{page_count} 页"),
        ("投标主体", profile.get("company", "")),
        ("审核日期", datetime.now().strftime("%Y-%m-%d")),
        ("风险统计", f"高风险 {n_high} 项 / 中风险 {n_med} 项 / 时间提示见第六章"),
        ("编制说明", "AI 辅助审核，正式投标决策须由商务与技术负责人复核签字"),
    ]

    sections = {
        "S1": {"paragraphs": [summary]},
        "S2": {"tables": [{"title": "表 2-1 废标风险清单（按等级排序）",
                           "headers": ["等级", "条款/位置", "页码", "风险说明", "我方情况", "处置建议"],
                           "rows": risk_rows}]},
        "S3": {"tables": [{"title": "表 3-1 资格要求逐项核查",
                           "headers": ["招标要求", "我方情况", "是否满足", "页码"],
                           "rows": qual_rows_doc}]},
        "S4": {"tables": [{"title": "表 4-1 技术参数响应对照",
                           "headers": ["参数项", "招标要求", "我方参数", "偏离情况", "实质性", "页码"],
                           "rows": tech_rows_doc}]},
        "S5": {"tables": [{"title": "表 5-1 签字盖章与格式要求清单",
                           "headers": ["页码", "条款", "要求说明"],
                           "rows": seal_rows}]},
        "S6": {"tables": [{"title": "表 6-1 关键时间节点",
                           "headers": ["事项", "截止时间", "距今天数", "紧急度", "页码"],
                           "rows": time_rows}]},
    }

    appendix = []
    if citations:
        appendix.append({
            "title": "附录：审核依据的废标风险审核要点",
            "headers": ["要点文档", "日期", "内容摘要"],
            "rows": [[c["doc"], c.get("effective_date", ""), c.get("note", "")] for c in citations[:4]],
        })
    return {"meta": meta, "sections": sections, "appendix": appendix}


def export_report(*, filename: str, page_count: int, profile: dict,
                  risks: list, qual_rows: list, tech_rows: list,
                  seals: list, timelines: list, summary: str,
                  citations: list, report_no: str, fmt: str = "docx") -> str:
    out_name = f"招标审核报告-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}.{fmt}"
    storage_path = f"exports/{out_name}"
    store = get_file_store()
    import tempfile

    data = build_doc_data(filename=filename, page_count=page_count, profile=profile,
                          risks=risks, qual_rows=qual_rows, tech_rows=tech_rows,
                          seals=seals, timelines=timelines, summary=summary,
                          citations=citations, report_no=report_no)
    suffix = f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if fmt == "pdf":
            render_to_pdf(str(TEMPLATE_AUDIT), data, tmp_path)
            mime = "application/pdf"
        else:
            render_to_docx(str(TEMPLATE_AUDIT), data, tmp_path)
            mime = DOCX_MIME
        store.save(storage_path, Path(tmp_path).read_bytes())
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    file_id = register_generated_file(out_name, storage_path, mime)
    return f"/api/files/{file_id}"


# 延迟导入避免循环
def register_generated_file(filename: str, storage_path: str, mime: str) -> str:
    from app.api.files import register_generated_file as _r
    return _r(filename, storage_path, mime)


# ===== SSE =====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_text(text: str, delay: float = 0.015) -> AsyncIterator[str]:
    i = 0
    while i < len(text):
        yield sse_event("chunk", {"text": text[i : i + 8]})
        i += 8
        await asyncio.sleep(delay)


def _rule_summary_text(qual_rows, tech_rows, seals, timelines) -> str:
    lines = ["资格核查：" + "；".join(
        f"{q['requirement'][:20]}={'满足' if q['match'] else '不满足/缺失'}（第{q['page']}页）" for q in qual_rows)]
    lines.append("技术对照：" + "；".join(
        f"{t['item']} 要求{t['required']}/我方{t['ours']}/{_DEVIATION_TEXT.get(t['deviation'])}（第{t['page']}页{'，实质性' if t.get('material') else ''}）"
        for t in tech_rows))
    lines.append("盖章格式：" + "；".join(f"{s['desc'][:30]}（第{s['page']}页）" for s in seals))
    lines.append("时间节点：" + "；".join(f"{t['event']} {t['deadline']}（第{t['page']}页）" for t in timelines))
    return "\n".join(lines)


# ===== 主入口 =====

async def handle(payload: dict, request: Request) -> StreamingResponse:
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)

    trace_id = audit.new_trace_id()
    start_ms = audit.now_ms()
    report_no = f"TA-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    file_id = str(inputs.get("file_id", "") or "").strip()
    focus = str(inputs.get("focus", "") or "").strip()
    profile, profile_note = load_profile(str(inputs.get("our_profile", "") or ""))

    async def gen() -> AsyncIterator[str]:
        yield sse_event("meta", {
            "trace_id": trace_id,
            "module": MODULE_CODE,
            "demo_mode": settings.DEMO_MODE,
            "real_rag": True,
            "built_in_sample": not file_id,
        })

        filename = ""
        try:
            # ===== ① 取文件 + 解析 =====
            filename, content = get_file_bytes(file_id)
            pages = parse_document(filename, content)
            page_count = len(pages)
            logger.info("[M02] file=%s pages=%d", filename, page_count)

            # ===== ② P6：非招标文件拒绝 =====
            is_tender, hint_hits = rules.is_tender_document(pages)
            if not is_tender:
                msg = (f"未识别到招标文件典型结构（仅命中 {hint_hits} 个招标特征词）。"
                       "为避免误判，暂不出具审核结论。请上传文本型招标文件 PDF/Word"
                       "（扫描影印件需先做 OCR）。")
                async for piece in _stream_text(msg):
                    yield piece
                yield sse_event("result", {"structured": _empty_structured(msg)})
                yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms,
                                         "tokens": {"in": 0, "out": 0}})
                audit.audit_log(module_code=MODULE_CODE, action="invoke",
                                question=filename, answer=msg, citations=[],
                                latency_ms=audit.now_ms() - start_ms,
                                status="success", trace_id=trace_id)
                return

            # ===== ③ 规则引擎确定性骨架 =====
            qual_req = rules.discover_qualifications(pages)
            tech_req = rules.discover_tech(pages)
            seals_req = rules.discover_seals(pages)
            timeline_req = rules.discover_timelines(pages)

            # ===== ④ RAG 审核要点 =====
            citations: List[Dict[str, Any]] = []
            try:
                query = "招标文件 废标风险 资格 审计财报 注册资本 技术参数负偏离 盖章签字 投标保证金 截止时间 " + focus
                hits = get_service().search(query, [COLLECTION], top_k=TOP_K)
                reranked = rerank(query, hits, top_n=RERANK_TOP_N)
                citations = [build_citation(c) for c in reranked]
                for cite in citations:
                    yield sse_event("citation", cite)
            except Exception as e:  # noqa: BLE001
                logger.warning("[M02] RAG failed: %s", e)

            # ===== ⑤ LLM 补充条款 + 结论 =====
            answer_text = ""
            llm_used = False
            parsed: Optional[dict] = None
            if settings.llm_ready:
                try:
                    picked = pick_clause_pages(pages)
                    messages = [
                        {"role": "system", "content": _load_prompt("system.md")},
                        {"role": "user", "content": (
                            _load_prompt("user.md")
                            .replace("{profile}", json.dumps(profile, ensure_ascii=False))
                            .replace("{rule_summary}", _rule_summary_text(
                                rules.compare_qualifications(qual_req, profile),
                                rules.compare_tech(tech_req, profile),
                                seals_req,
                                rules.enrich_timelines(timeline_req, datetime.now())))
                            .replace("{clauses}", format_clauses(picked))
                            .replace("{focus}", focus or "无")
                        )},
                    ]
                    collected: List[str] = []
                    for piece in get_gateway().chat_stream(
                        messages, module_code=MODULE_CODE, temperature=0.2, max_tokens=3000
                    ):
                        collected.append(piece)
                        if stream:
                            yield sse_event("chunk", {"text": piece})
                    answer_text = "".join(collected)
                    parsed = extract_json(answer_text)
                    llm_used = parsed is not None
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M02] LLM failed, pure-rules mode: %s", e)

            # ===== ⑥ 合并 LLM 补集 → 最终比对 =====
            eq, et, es, etm = merge_llm_extras(parsed, pages, profile,
                                               qual_req, tech_req, seals_req, timeline_req)
            qual_req_all = qual_req + eq
            tech_req_all = tech_req + et
            seals_all = seals_req + es
            timeline_all = timeline_req + etm

            qual_rows = rules.compare_qualifications(qual_req_all, profile)
            tech_rows = rules.compare_tech(tech_req_all, profile)
            timelines = rules.enrich_timelines(timeline_all, datetime.now())
            risks = rules.build_risks(qual_rows, tech_rows, seals_all, timelines)

            # ===== ⑦ 结论（LLM 或模板）=====
            if parsed and parsed.get("summary"):
                summary = str(parsed["summary"])
                if not stream:
                    yield sse_event("chunk", {"text": summary})
            else:
                summary = build_summary(risks, qual_rows, tech_rows)
                if profile_note:
                    summary += f"（注：企业档案文本未能按 JSON 解析，本次比对使用内置档案；原文摘要：{profile_note[:80]}）"
                async for piece in _stream_text(summary):
                    yield piece

            structured = {
                "audit_summary": summary,
                "disqualify_risks": [{k: v for k, v in r.items() if k != "id"} for r in risks],
                "qualification_check": [
                    {"requirement": q["requirement"], "ours": q["ours"],
                     "match": q["match"], "page": q["page"]} for q in qual_rows],
                "tech_response": [
                    {"item": t["item"], "required": t["required"], "ours": t["ours"],
                     "deviation": _DEVIATION_TEXT.get(t["deviation"], t["deviation"]),
                     "material": t["material"], "page": t["page"]} for t in tech_rows],
                "seal_required": [
                    {"page": s["page"], "clause": s.get("clause", ""), "desc": s["desc"]}
                    for s in seals_all],
                "timeline": [
                    {"event": t["event"], "deadline": t["deadline"],
                     "days_left": t["days_left"], "level": _LEVEL_TEXT.get(t["level"], t["level"]),
                     "page": t["page"]} for t in timelines],
                "citations": citations,
                "llm_used": llm_used,
                "report_no": report_no,
                "file_name": filename,
                "page_count": page_count,
            }

            # ===== ⑧ Word / PDF 导出 =====
            try:
                structured["export_url"] = export_report(
                    filename=filename, page_count=page_count, profile=profile,
                    risks=risks, qual_rows=qual_rows, tech_rows=tech_rows,
                    seals=seals_all, timelines=timelines, summary=summary,
                    citations=citations, report_no=report_no)
            except Exception as e:  # noqa: BLE001
                logger.exception("[M02] docx export failed: %s", e)
                structured["export_url"] = ""
            try:
                structured["export_url_pdf"] = export_report(
                    filename=filename, page_count=page_count, profile=profile,
                    risks=risks, qual_rows=qual_rows, tech_rows=tech_rows,
                    seals=seals_all, timelines=timelines, summary=summary,
                    citations=citations, report_no=report_no, fmt="pdf")
            except Exception as e:  # noqa: BLE001
                logger.exception("[M02] pdf export failed: %s", e)
                structured["export_url_pdf"] = ""

            yield sse_event("result", {"structured": structured})
            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(module_code=MODULE_CODE, action="invoke",
                            question=f"{filename} | {focus}", answer=answer_text[:4000] or summary,
                            citations=citations, latency_ms=latency,
                            status="success" if llm_used else "degraded", trace_id=trace_id)

        except FileNotFoundError as e:
            msg = str(e)
            async for piece in _stream_text(msg):
                yield piece
            yield sse_event("result", {"structured": _empty_structured(msg)})
            yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms,
                                     "tokens": {"in": 0, "out": 0}})
            audit.audit_log(module_code=MODULE_CODE, action="invoke",
                            question=file_id, answer=msg, citations=[],
                            latency_ms=audit.now_ms() - start_ms,
                            status="failed", trace_id=trace_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("[M02] handle failed: %s", e)
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(module_code=MODULE_CODE, action="invoke",
                            question=file_id or filename, answer=str(e), citations=[],
                            latency_ms=audit.now_ms() - start_ms,
                            status="failed", trace_id=trace_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _empty_structured(note: str) -> dict:
    return {
        "audit_summary": note,
        "disqualify_risks": [],
        "qualification_check": [],
        "tech_response": [],
        "seal_required": [],
        "timeline": [],
        "export_url": "",
        "note": note,
    }
