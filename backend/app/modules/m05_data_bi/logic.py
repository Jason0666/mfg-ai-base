"""M05 生产数据问答模块业务逻辑（§9.5）。

真实 NL2SQL 链路（M2 DoD 要求，不走 demo 短路）：

1. 输入解析：question（必填）、time_range（选填）
2. 表结构白名单：从只读 RDB adapter 取 biz_prod_metric 实际 schema
3. LLM 生成 SQL：外置 prompts + 表结构 + 时间范围 → 一条 SELECT
4. SQL 安全校验：core.nl2sql.validate_sql（仅 SELECT / 危险字符 / 表白名单）
   + ensure_limit（自动补 LIMIT 1000）；校验/执行失败反馈 LLM 重试一次
5. 只读执行：通过 adapter.query（adapter 内部还有一层只读护栏）
6. 结果解读：LLM 基于真实数据输出 insight + attribution + chart 提示（严格 JSON）
7. 图表组装：后端按 chart 提示用真实查询结果组装 ECharts option（数字不经过 LLM 手填）
8. 空结果：明确提示「未查询到符合条件的数据」，不编造（P6）

LLM 不可用时：降级使用 demo_scripts.json 的预置 SQL（仍走安全校验与真实执行），
结论用预置文本——数据永远来自真实查询。

SSE 事件序列：meta → chunk*（insight 打字机）→ result（sql/table_data/chart_spec/...）→ done
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import yaml
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.adapters.factory import get_relational_db
from app.config import settings
from app.core import audit
from app.core.llm_gateway import get_gateway
from app.core.nl2sql import describe_schema, ensure_limit, extract_sql, validate_sql

logger = logging.getLogger(__name__)

MODULE_CODE = "m05_data_bi"
MODULE_DIR = Path(__file__).resolve().parent

# 表白名单（§9.5 强制：表名必须命中）
TABLE_WHITELIST = {"biz_prod_metric"}
MAX_ROWS = 1000
ROWS_PREVIEW_TO_LLM = 300


# ===== manifest 加载 =====

def load_manifest() -> dict:
    p = MODULE_DIR / "manifest.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ===== 时间范围解析 =====

def resolve_time_range(time_range: str) -> Tuple[str, str, str]:
    """把时间范围选项解析为 (展示描述, 起始日期, 截止日期)。

    截止日期不含时返回空串表示不限制。
    """
    today = date.today()
    tr = (time_range or "全部").strip()
    if tr == "近7天":
        start = today - timedelta(days=6)
        desc = f"{tr}（{start.isoformat()} 至 {today.isoformat()}，含当天）"
        return desc, start.isoformat(), today.isoformat()
    if tr == "上周":
        # 周一为一周开始
        monday_this_week = today - timedelta(days=today.weekday())
        last_mon = monday_this_week - timedelta(days=7)
        last_sun = monday_this_week - timedelta(days=1)
        return f"{tr}（{last_mon.isoformat()} 至 {last_sun.isoformat()}）", last_mon.isoformat(), last_sun.isoformat()
    if tr == "近30天":
        start = today - timedelta(days=29)
        return f"{tr}（{start.isoformat()} 至 {today.isoformat()}，含当天）", start.isoformat(), today.isoformat()
    # 全部：不强制日期限制（种子数据覆盖 2026-06-19 ~ 2026-09-16）
    return "全部可用数据（2026-06-19 至 2026-09-16）", "", ""


# ===== Prompt 构造 =====

def _load_prompt(name: str) -> str:
    p = MODULE_DIR / "prompts" / name
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def build_sql_messages(question: str, time_range_desc: str, schema_text: str) -> List[Dict[str, str]]:
    system = _load_prompt("sql_system.md")
    user = (
        _load_prompt("sql_user.md")
        .replace("{question}", question)
        .replace("{time_range_desc}", time_range_desc)
        .replace("{schema}", schema_text)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_interpret_messages(
    question: str,
    time_range_desc: str,
    sql: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    preview = rows[:ROWS_PREVIEW_TO_LLM]
    system = _load_prompt("interpret_system.md")
    user = (
        _load_prompt("interpret_user.md")
        .replace("{question}", question)
        .replace("{time_range_desc}", time_range_desc)
        .replace("{sql}", sql)
        .replace("{row_count}", str(len(rows)))
        .replace("{preview_limit}", str(ROWS_PREVIEW_TO_LLM))
        .replace("{rows_json}", json.dumps(preview, ensure_ascii=False, indent=2))
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ===== SQL 生成 + 校验 + 执行（含一轮错误反馈重试）=====

def generate_and_run_sql(
    question: str,
    time_range_desc: str,
    schema_text: str,
    force_sql: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    """生成/校验/执行 SQL。返回 (sql, rows, error)。

    force_sql：降级剧本预置 SQL 或重试时的固定 SQL。
    error 非空表示两轮均失败。
    """
    gateway = get_gateway()
    sql = force_sql or ""

    for attempt in range(2):
        if not sql:
            messages = build_sql_messages(question, time_range_desc, schema_text)
            if attempt > 0:
                # 重试：追加上一轮错误反馈
                messages.append({"role": "user", "content": f"上一条 SQL 无法执行：{last_error}。请修正后只输出一条新的 SELECT。"})
            raw = gateway.chat(
                messages,
                module_code=MODULE_CODE,
                temperature=0.0,
                max_tokens=600,
            )
            sql = extract_sql(raw) or ""
        if not sql:
            last_error = "模型未返回可解析的 SQL"
            sql = ""
            continue
        try:
            validate_sql(sql, TABLE_WHITELIST)
            sql = ensure_limit(sql, MAX_ROWS)
            rows = get_relational_db().query(sql)
            return sql, rows, None
        except ValueError as e:
            # 安全校验拒绝：反馈重试（不执行）
            logger.warning("[M05] SQL rejected (attempt %d): %s | sql=%s", attempt + 1, e, sql[:120])
            last_error = str(e)
            sql = ""
            continue
        except Exception as e:  # noqa: BLE001
            # 执行错误：反馈重试
            logger.warning("[M05] SQL execute failed (attempt %d): %s | sql=%s", attempt + 1, e, sql[:120])
            last_error = f"执行报错：{e}"
            sql = ""
            continue

    return "", [], last_error


# ===== 解读 JSON 解析 =====

def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    import re
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
        import re as _re
        t2 = _re.sub(r",\s*([}\]])", r"\1", t)
        try:
            return json.loads(t2)
        except json.JSONDecodeError:
            return None


# ===== ECharts option 组装（用真实数据，LLM 只给图表意图）=====

def build_chart_spec(chart_hint: Optional[dict], rows: List[Dict[str, Any]]) -> Optional[dict]:
    """按 LLM 的图表意图 + 真实查询结果，组装完整 ECharts option。"""
    if not chart_hint or not isinstance(chart_hint, dict) or not rows:
        return None
    ctype = chart_hint.get("type")
    if ctype not in ("line", "bar"):
        return None
    x_field = chart_hint.get("x_field")
    series_def = chart_hint.get("series") or []
    group_by = chart_hint.get("group_by")
    if not x_field or not series_def or x_field not in rows[0]:
        return None

    # 兜底自动发现分组列：LLM 未给 group_by，但同一 x 存在多行（长表）时，
    # 按常见分类列自动分组，避免多条产线数据互相覆盖成一条线
    x_values_all = [r.get(x_field) for r in rows]
    if not group_by and len(x_values_all) != len(set(x_values_all)):
        for cand in ("line", "产线", "group", "category", "series"):
            if cand in rows[0] and cand != x_field:
                vals = {r.get(cand) for r in rows}
                if 1 < len(vals) <= 20:
                    group_by = cand
                    break

    title = str(chart_hint.get("title", ""))
    # x 轴按值排序去重
    x_values = sorted({str(r.get(x_field)) for r in rows if r.get(x_field) is not None})

    echarts_series: List[Dict[str, Any]] = []
    legend_data: List[str] = []

    if group_by and group_by in rows[0]:
        # 长表分组：每个组一条 series，值取第一个 series_def.field
        value_field = series_def[0].get("field")
        base_name = series_def[0].get("name", value_field)
        if not value_field or value_field not in rows[0]:
            return None
        groups = sorted({str(r.get(group_by)) for r in rows if r.get(group_by) is not None})
        for g in groups:
            name = f"{g}{base_name}" if base_name and base_name not in g else g
            lookup = {str(r.get(x_field)): r.get(value_field) for r in rows if str(r.get(group_by)) == g}
            data = [lookup.get(x) for x in x_values]
            legend_data.append(name)
            echarts_series.append({
                "name": name,
                "type": ctype,
                "data": data,
                "connectNulls": True,
                "smooth": ctype == "line",
            })
    else:
        for sd in series_def:
            field = sd.get("field")
            if not field or field not in rows[0]:
                continue
            name = str(sd.get("name", field))
            legend_data.append(name)
            echarts_series.append({
                "name": name,
                "type": ctype,
                "data": [r.get(field) for r in rows],
                "smooth": ctype == "line",
            })

    if not echarts_series:
        return None

    return {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data, "bottom": 0},
        "grid": {"left": 50, "right": 20, "top": 40, "bottom": 40},
        "xAxis": {"type": "category", "data": x_values, "boundaryGap": ctype == "bar"},
        "yAxis": {"type": "value", "scale": True},
        "series": echarts_series,
    }


# ===== SSE 工具 =====

def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_text(text: str, delay: float = 0.02) -> AsyncIterator[str]:
    i = 0
    while i < len(text):
        chunk = text[i : i + 4]
        yield sse_event("chunk", {"text": chunk})
        i += 4
        await asyncio.sleep(delay)


# ===== DEMO 剧本降级 =====

def _load_demo_scripts() -> list[dict]:
    p = MODULE_DIR / "seed" / "demo_scripts.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _match_demo_script(question: str) -> Optional[dict]:
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
    """模块统一 invoke 入口（真实 NL2SQL 链路）。"""
    inputs = payload.get("inputs", {}) or {}
    stream = payload.get("stream", True)
    question = str(inputs.get("question", "")).strip()
    time_range = str(inputs.get("time_range", "全部") or "全部").strip()

    trace_id = audit.new_trace_id()
    start_ms = audit.now_ms()

    async def gen() -> AsyncIterator[str]:
        yield sse_event("meta", {
            "trace_id": trace_id,
            "module": MODULE_CODE,
            "demo_mode": settings.DEMO_MODE,
            "real_nl2sql": True,
        })

        try:
            if not question:
                msg = "请输入问题后再提交。"
                async for piece in _stream_text(msg):
                    yield piece
                yield sse_event("result", {"structured": {"note": msg}})
                yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms, "tokens": {"in": 0, "out": 0}})
                return

            time_range_desc, range_start, range_end = resolve_time_range(time_range)
            logger.info("[M05] question=%r range=%s", question, time_range_desc)

            # ===== ① 取白名单表实际结构 =====
            db = get_relational_db()
            schema = db.get_schema(list(TABLE_WHITELIST))
            schema_text = describe_schema(schema)

            # ===== ② 生成 SQL（LLM 或降级预置）→ 校验 → 执行 =====
            sql = ""
            rows: List[Dict[str, Any]] = []
            sql_error: Optional[str] = None
            llm_used = False

            if settings.llm_ready:
                try:
                    sql, rows, sql_error = generate_and_run_sql(
                        question, time_range_desc, schema_text
                    )
                    llm_used = not sql_error
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M05] LLM sql generation failed: %s", e)
                    sql_error = str(e)

            # 降级：LLM 不可用或两轮失败 → 预置 SQL（仍真实校验+执行）
            if not sql:
                scripted = _match_demo_script(question)
                if scripted and scripted.get("sql"):
                    try:
                        validate_sql(scripted["sql"], TABLE_WHITELIST)
                        sql = ensure_limit(scripted["sql"], MAX_ROWS)
                        rows = db.query(sql)
                        sql_error = None
                        logger.info("[M05] fallback demo SQL executed: %s", sql[:100])
                    except Exception as e:  # noqa: BLE001
                        sql_error = f"降级 SQL 执行失败：{e}"
                elif sql_error:
                    # 无降级可用：安全拒绝，不编造
                    msg = f"无法生成安全可执行的查询：{sql_error}。请换一种问法或联系管理员。"
                    async for piece in _stream_text(msg):
                        yield piece
                    yield sse_event("result", {"structured": {"note": msg, "sql": ""}})
                    latency = audit.now_ms() - start_ms
                    yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                    audit.audit_log(
                        module_code=MODULE_CODE, action="invoke", question=question,
                        answer=msg, latency_ms=latency, status="failed", trace_id=trace_id,
                    )
                    return
                else:
                    msg = "未能理解该问题，请换一种问法（如：上周各产线良率对比）。"
                    async for piece in _stream_text(msg):
                        yield piece
                    yield sse_event("result", {"structured": {"note": msg, "sql": ""}})
                    yield sse_event("done", {"latency_ms": audit.now_ms() - start_ms, "tokens": {"in": 0, "out": 0}})
                    return

            # ===== ③ 空结果 → P6 =====
            if not rows:
                msg = f"未查询到符合条件的数据（时间范围：{time_range_desc}）。请调整时间范围或问题后重试。"
                async for piece in _stream_text(msg):
                    yield piece
                yield sse_event("result", {"structured": {"sql": sql, "table_data": [], "note": msg}})
                latency = audit.now_ms() - start_ms
                yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
                audit.audit_log(
                    module_code=MODULE_CODE, action="invoke", question=question,
                    answer=msg, latency_ms=latency, status="success", trace_id=trace_id,
                )
                return

            logger.info("[M05] SQL rows=%d sql=%s", len(rows), sql[:120])

            # ===== ④ LLM 解读 + 归因 + 图表意图 =====
            insight = ""
            attribution: List[dict] = []
            chart_spec: Optional[dict] = None
            interpret_ok = False

            if settings.llm_ready:
                try:
                    messages = build_interpret_messages(question, time_range_desc, sql, rows)
                    raw = get_gateway().chat(
                        messages,
                        module_code=MODULE_CODE,
                        temperature=0.2,
                        max_tokens=1500,
                    )
                    parsed = _extract_json(raw)
                    if parsed:
                        insight = str(parsed.get("insight", "")).strip()
                        attribution = parsed.get("attribution") or []
                        chart_spec = build_chart_spec(parsed.get("chart"), rows)
                        interpret_ok = True
                except Exception as e:  # noqa: BLE001
                    logger.warning("[M05] interpret failed: %s", e)

            # 降级解读：预置文本 + 预置图表意图（数据仍是真实查询结果）
            if not interpret_ok:
                scripted = _match_demo_script(question)
                if scripted:
                    insight = scripted.get("insight", "")
                    attribution = scripted.get("attribution", [])
                    chart_spec = build_chart_spec(scripted.get("chart"), rows)
                if not insight:
                    insight = f"（模型不可用）查询已真实执行，返回 {len(rows)} 行数据，请查看下方表格。"

            # ===== ⑤ 流式输出 insight 文本 =====
            if stream:
                async for piece in _stream_text(insight):
                    yield piece
            else:
                yield sse_event("chunk", {"text": insight})

            # ===== ⑥ result：完整结构化输出 =====
            structured = {
                "sql": sql,
                "table_data": rows,
                "chart_spec": chart_spec,
                "insight": insight,
                "attribution": attribution,
                "llm_used": llm_used,
            }
            yield sse_event("result", {"structured": structured})

            latency = audit.now_ms() - start_ms
            yield sse_event("done", {"latency_ms": latency, "tokens": {"in": 0, "out": 0}})
            audit.audit_log(
                module_code=MODULE_CODE, action="invoke", question=question,
                answer=insight[:4000], latency_ms=latency,
                status="success" if llm_used else "degraded", trace_id=trace_id,
            )

        except Exception as e:  # noqa: BLE001
            logger.exception("[M05] handle failed: %s", e)
            yield sse_event("error", {"code": "internal_error", "message": str(e)})
            audit.audit_log(
                module_code=MODULE_CODE, action="invoke", question=question,
                answer=str(e), status="failed", trace_id=trace_id,
            )

    return StreamingResponse(gen(), media_type="text/event-stream")
