"""访客时效访问（§2.1）：签名令牌 + 四项校验 + 三重防消耗。

令牌形态：HS256 JWT（role=guest，携带 jti/exp），复用平台 jwt_secret。
持久化：app_guest_token 表（jti/note/exp/max_calls/used_calls/revoked/...）。

四项校验（缺一不可）：签名有效 → 未过期（服务端 exp）→ 未吊销 → 未超次。
三重防消耗：时效 + 次数上限（默认 30，超限 429）+ 频率限制（每分钟 5 次 invoke）。

降级策略：访客默认强制剧本模式（不调真实大模型）；高级访客（allow_real_llm）
可消耗真实 LLM 配额 real_llm_max 次，超出后自动降级回剧本模式。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.core.db import get_conn, ph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JWT（与 core.auth 同构的 HS256 手写实现，区分 role=guest）
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(s: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_guest_jwt(jti: str, exp: int) -> str:
    """签发访客 JWT：payload 仅含 role/jti/iat/exp（不含用户信息）。"""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"role": "guest", "jti": jti, "iat": int(time.time()), "exp": int(exp)}
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(settings.jwt_secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def decode_guest_jwt(token: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """校验签名与 exp。返回 (payload, err)：err ∈ "" | "invalid" | "expired"。"""
    try:
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}".encode()
        expect = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expect, _b64url_decode(s)):
            return None, "invalid"
        payload = json.loads(_b64url_decode(p))
        if payload.get("role") != "guest" or not payload.get("jti"):
            return None, "invalid"
        if int(payload.get("exp", 0)) < time.time():
            return None, "expired"
        return payload, ""
    except Exception:  # noqa: BLE001
        return None, "invalid"


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def create_token(
    note: str = "",
    hours: int = 0,
    max_calls: int = 0,
    allow_real_llm: bool = False,
    real_llm_max: int = 0,
) -> Dict[str, Any]:
    """生成访客令牌记录并签发 JWT。返回含 token 的新记录。"""
    hours = hours if hours in (1, 6, 24) else settings.GUEST_DEFAULT_HOURS
    max_calls = max(1, int(max_calls or settings.GUEST_MAX_CALLS))
    real_llm_max = max(1, int(real_llm_max or settings.GUEST_REAL_LLM_MAX))
    jti = uuid.uuid4().hex
    exp = int(time.time()) + hours * 3600
    conn = get_conn()
    try:
        conn.execute(
            f"INSERT INTO app_guest_token "
            f"(jti, note, exp, max_calls, used_calls, allow_real_llm, real_llm_max, real_llm_used, revoked) "
            f"VALUES ({ph()}, {ph()}, {ph()}, {ph()}, 0, {ph()}, {ph()}, 0, 0)",
            (jti, note[:200], exp, max_calls, 1 if allow_real_llm else 0, real_llm_max),
        )
        conn.commit()
    finally:
        conn.close()
    row = get_by_jti(jti)
    row["token"] = issue_guest_jwt(jti, exp)
    return row


def get_by_jti(jti: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.execute(f"SELECT * FROM app_guest_token WHERE jti={ph()}", (jti,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_tokens() -> list:
    conn = get_conn()
    try:
        cur = conn.execute(
            f"SELECT * FROM app_guest_token ORDER BY id DESC LIMIT 200"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def revoke(jti: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            f"UPDATE app_guest_token SET revoked=1 WHERE jti={ph()} AND revoked=0",
            (jti,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def incr_used(jti: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE app_guest_token SET used_calls = used_calls + 1 WHERE jti={ph()}",
            (jti,),
        )
        conn.commit()
    finally:
        conn.close()


def incr_real_used(jti: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE app_guest_token SET real_llm_used = real_llm_used + 1 WHERE jti={ph()}",
            (jti,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 四项校验（签名/有效期/吊销/配额）→ (err, info)
# err ∈ "" | "invalid" | "expired" | "revoked" | "quota"
# ---------------------------------------------------------------------------
def check(token: str) -> Tuple[str, Dict[str, Any]]:
    payload, err = decode_guest_jwt(token or "")
    if err:
        return err, {}
    row = get_by_jti(str(payload["jti"]))
    if row is None:
        return "invalid", {}
    if row.get("revoked"):
        return "revoked", {}
    now = time.time()
    if now >= int(row["exp"]):
        return "expired", {}
    if int(row["used_calls"]) >= int(row["max_calls"]):
        return "quota", {}
    return "", {
        "jti": row["jti"],
        "note": row.get("note") or "",
        "exp": int(row["exp"]),
        "remaining_seconds": max(0, int(row["exp"]) - int(now)),
        "max_calls": int(row["max_calls"]),
        "used_calls": int(row["used_calls"]),
        "allow_real_llm": bool(row.get("allow_real_llm")),
        "real_llm_max": int(row.get("real_llm_max") or 0),
        "real_llm_used": int(row.get("real_llm_used") or 0),
    }


def info_of(row_or_info: Dict[str, Any]) -> Dict[str, Any]:
    """标准化访客信息（供中间件/接口透出）。"""
    now = time.time()
    return {
        "jti": row_or_info["jti"],
        "note": row_or_info.get("note") or "",
        "exp": int(row_or_info["exp"]),
        "remaining_seconds": max(0, int(row_or_info["exp"]) - int(now)),
        "max_calls": int(row_or_info["max_calls"]),
        "used_calls": int(row_or_info.get("used_calls") or 0),
        "allow_real_llm": bool(row_or_info.get("allow_real_llm")),
        "real_llm_max": int(row_or_info.get("real_llm_max") or 0),
        "real_llm_used": int(row_or_info.get("real_llm_used") or 0),
    }


# ---------------------------------------------------------------------------
# 频率限制（三重保险之三）：每 token 每分钟 GUEST_RATE_PER_MINUTE 次 invoke
# ---------------------------------------------------------------------------
_invoke_windows: Dict[str, deque] = defaultdict(deque)


def allow_invoke(jti: str) -> bool:
    now = time.time()
    win = _invoke_windows[jti]
    while win and now - win[0] > 60:
        win.popleft()
    if len(win) >= settings.GUEST_RATE_PER_MINUTE:
        return False
    win.append(now)
    return True
