"""审计日志（§5.4、§C5）。

所有用户可见的 AI 输出必须经过 audit_log 记录。
M3：写 sqlite/pg app_audit_log 持久化（失败回退内存 buffer）+ 筛选查询 + 统计。

user_id 注入：main.py 中间件在请求开始 set_current_user(uid)，结束 reset；
模块 logic 调用 audit_log() 无需显式传 user_id（保持 M1 签名兼容）。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 进程内审计缓冲（DB 写失败时的兜底 + M1 兼容）
_audit_buffer: list[dict] = []
_MAX_BUFFER = 1000

# 当前请求用户（contextvar，由中间件注入）
_current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)


def set_current_user(user_id: Optional[int]) -> Any:
    return _current_user_id.set(user_id)


def reset_current_user(token: Any) -> None:
    _current_user_id.reset(token)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def audit_log(
    *,
    user_id: Optional[int] = None,
    module_code: str = "",
    action: str = "invoke",
    question: Optional[str] = None,
    answer: Optional[str] = None,
    citations: Optional[list[dict]] = None,
    model: Optional[str] = None,
    latency_ms: Optional[int] = None,
    token_in: Optional[int] = None,
    token_out: Optional[int] = None,
    status: str = "success",  # success | failed | degraded
    trace_id: Optional[str] = None,
) -> str:
    """记录一条审计日志。返回 trace_id。

    即使 settings.AUDIT_ENABLED=false 也会返回 trace_id（用于链路追踪）。
    user_id 缺省时自动取当前请求上下文（contextvar）。
    """
    tid = trace_id or new_trace_id()
    if not settings.AUDIT_ENABLED:
        return tid

    uid = user_id if user_id is not None else _current_user_id.get()
    entry = {
        "id": uuid.uuid4().hex,
        "user_id": uid,
        "module_code": module_code,
        "action": action,
        "question": (question or "")[:4000],
        "answer": (answer or "")[:4000],
        "citations": citations or [],
        "model": model,
        "latency_ms": latency_ms,
        "token_in": token_in,
        "token_out": token_out,
        "status": status,
        "trace_id": tid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 内存 buffer（兜底，始终写）
    _audit_buffer.append(entry)
    if len(_audit_buffer) > _MAX_BUFFER:
        _audit_buffer.pop(0)

    # DB 持久化（失败仅告警，不影响业务）
    try:
        from app.core.db import get_conn, ph

        conn = get_conn()
        try:
            conn.execute(
                f"INSERT INTO app_audit_log "
                f"(user_id, module_code, action, question, answer, citations, model, "
                f" latency_ms, token_in, token_out, status, trace_id, created_at) "
                f"VALUES ({ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, "
                f"        {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()})",
                (
                    uid,
                    module_code,
                    action,
                    entry["question"],
                    entry["answer"],
                    json.dumps(entry["citations"], ensure_ascii=False),
                    model,
                    latency_ms,
                    token_in,
                    token_out,
                    status,
                    tid,
                    entry["created_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("audit persist failed (keep in-memory): %s", e)

    logger.info(
        "[AUDIT] module=%s action=%s status=%s latency=%sms trace=%s",
        module_code, action, status, latency_ms, tid,
    )
    return tid


def get_recent_audit(limit: int = 100) -> list[dict]:
    """返回最近的审计记录（M1 兼容接口，读 DB 优先）。"""
    try:
        result = query_audit(limit=limit)
        return list(result["items"])
    except Exception:  # noqa: BLE001 DB 不可用时回退内存
        return list(reversed(_audit_buffer[-limit:]))


# ---------------------------------------------------------------------------
# M3：筛选查询与统计
# ---------------------------------------------------------------------------
def query_audit(
    module: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    user_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """条件查询审计日志，返回 {items, total}。q 对 question/answer 模糊匹配。"""
    from app.core.db import get_conn, ph

    where: list[str] = []
    params: list[Any] = []
    if module:
        where.append(f"a.module_code = {ph()}")
        params.append(module)
    if status:
        where.append(f"a.status = {ph()}")
        params.append(status)
    if action:
        where.append(f"a.action = {ph()}")
        params.append(action)
    if user_id is not None:
        where.append(f"a.user_id = {ph()}")
        params.append(user_id)
    if q:
        where.append(f"(a.question LIKE {ph()} OR a.answer LIKE {ph()})")
        like = f"%{q}%"
        params.extend([like, like])
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_conn()
    try:
        cur = conn.execute(f"SELECT COUNT(*) AS c FROM app_audit_log a{where_sql}", params)
        total = cur.fetchone()["c"]
        # LEFT JOIN app_user 拿 username，前端显示真实用户名而非"用户N"
        cur = conn.execute(
            f"SELECT a.*, u.username AS username "
            f"FROM app_audit_log a "
            f"LEFT JOIN app_user u ON a.user_id = u.id "
            f"{where_sql} "
            f"ORDER BY a.created_at DESC, a.id DESC LIMIT {int(limit)} OFFSET {int(offset)}",
            params,
        )
        items = [dict(r) for r in cur.fetchall()]
        for it in items:
            it["citations"] = _load_citations(it.get("citations"))
        return {"items": items, "total": total}
    finally:
        conn.close()


def _load_citations(raw: Any) -> Any:
    """citations 列兼容 TEXT(JSON 字符串) 与 JSONB(dict)。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return []


def stats() -> Dict[str, Any]:
    """调用统计：总量/状态分布/平均延迟/模块分布 top/近7日趋势。"""
    from app.core.db import get_conn, ph

    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) AS c, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
            "SUM(CASE WHEN status='degraded' THEN 1 ELSE 0 END) AS degraded, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "AVG(latency_ms) AS avg_latency "
            "FROM app_audit_log"
        )
        row = cur.fetchone()

        cur = conn.execute(
            "SELECT module_code, COUNT(*) AS c FROM app_audit_log "
            "WHERE module_code IS NOT NULL AND module_code != '' "
            "GROUP BY module_code ORDER BY c DESC LIMIT 8"
        )
        by_module = [{"module": r["module_code"], "count": r["c"]} for r in cur.fetchall()]

        # 近 7 日每日计数（sqlite 用 substr，pg 用 ::text LIKE 前缀）
        trend: list[dict] = []
        import datetime as _dt

        for i in range(6, -1, -1):
            day = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
            if settings.DB_DRIVER == "postgres":
                sql = f"SELECT COUNT(*) AS c FROM app_audit_log WHERE created_at::text LIKE {ph()}"
            else:
                sql = f"SELECT COUNT(*) AS c FROM app_audit_log WHERE substr(created_at,1,10) = {ph()}"
            cur = conn.execute(sql, (f"{day}%",) if settings.DB_DRIVER == "postgres" else (day,))
            trend.append({"date": day, "count": cur.fetchone()["c"]})

        total = row["c"] or 0
        return {
            "total_invocations": total,
            "success": row["success"] or 0,
            "degraded": row["degraded"] or 0,
            "failed": row["failed"] or 0,
            "avg_latency_ms": int(row["avg_latency"] or 0),
            "by_module": by_module,
            "trend_7d": trend,
        }
    finally:
        conn.close()


def now_ms() -> int:
    return int(time.time() * 1000)
