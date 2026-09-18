"""平台表持久化（§6.1）：app_user / app_module_permission / app_audit_log / app_module。

sqlite（默认，DEMO 与单机交付）与 postgres（Docker 交付）双兼容：
- get_conn() 返回原生连接（行已转 dict 语义，sqlite3.Row / psycopg2 RealDictRow）
- ph() 适配占位符：sqlite `?`、pg `%s`
- ensure_platform_tables() 幂等建表（双方言 DDL）
- bootstrap_users() 首启种子三角色账号
- sync_modules() 运行时把 manifest 同步进 app_module

业务表（biz_prod_metric 等）仍由 seed/load_seed.py 自管，不在此处。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _sqlite_path() -> str:
    """解析 sqlite 绝对路径（uvicorn 工作目录为 backend/，与 load_seed 同规则）。"""
    p = Path(settings.SQLITE_PATH)
    if not p.is_absolute():
        p = Path(settings.BASE_DIR).resolve() / settings.SQLITE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def get_conn() -> Any:
    """返回数据库连接（调用方负责 close）。

    sqlite：conn.row_factory = sqlite3.Row，可用 row["col"] 取值；
    postgres：RealDictCursor 行为即 dict。
    """
    if settings.DB_DRIVER == "postgres":
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
        )
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    conn = sqlite3.connect(_sqlite_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ph(*index: int) -> str:
    """占位符：sqlite `?`；pg `%s`。用法：f"... VALUES ({ph()}, {ph()}, ...)"."""
    if settings.DB_DRIVER == "postgres":
        return "%s"
    return "?"


def executescript_safe(conn: Any, statements: Iterable[str]) -> None:
    for sql in statements:
        conn.execute(sql)


# ---------------------------------------------------------------------------
# 建表（§6.1 DDL，双方言）
# ---------------------------------------------------------------------------
_DDL_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS app_module (
        code        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        category    TEXT NOT NULL,
        version     TEXT NOT NULL,
        enabled     INTEGER DEFAULT 1,
        manifest    TEXT NOT NULL,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_user (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        display_name  TEXT,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL,
        enabled       INTEGER DEFAULT 1,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_module_permission (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES app_user(id),
        module_code TEXT NOT NULL,
        UNIQUE(user_id, module_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_audit_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        module_code TEXT,
        action      TEXT NOT NULL,
        question    TEXT,
        answer      TEXT,
        citations   TEXT,
        model       TEXT,
        latency_ms  INTEGER,
        token_in    INTEGER,
        token_out   INTEGER,
        status      TEXT NOT NULL,
        trace_id    TEXT,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_module_time ON app_audit_log(module_code, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON app_audit_log(created_at)",
    # 访客时效令牌（§2.1）：jti 持久化，支撑吊销/配额/过期四项校验
    """
    CREATE TABLE IF NOT EXISTS app_guest_token (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        jti            TEXT UNIQUE NOT NULL,
        note           TEXT DEFAULT '',
        exp            INTEGER NOT NULL,
        max_calls      INTEGER NOT NULL DEFAULT 30,
        used_calls     INTEGER NOT NULL DEFAULT 0,
        allow_real_llm INTEGER NOT NULL DEFAULT 0,
        real_llm_max   INTEGER NOT NULL DEFAULT 10,
        real_llm_used  INTEGER NOT NULL DEFAULT 0,
        revoked        INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_guest_jti ON app_guest_token(jti)",
]

_DDL_PG = [
    """
    CREATE TABLE IF NOT EXISTS app_module (
        code        VARCHAR(64) PRIMARY KEY,
        name        VARCHAR(128) NOT NULL,
        category    VARCHAR(32) NOT NULL,
        version     VARCHAR(16) NOT NULL,
        enabled     BOOLEAN DEFAULT TRUE,
        manifest    JSONB NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_user (
        id            BIGSERIAL PRIMARY KEY,
        username      VARCHAR(64) UNIQUE NOT NULL,
        display_name  VARCHAR(64),
        password_hash VARCHAR(256) NOT NULL,
        role          VARCHAR(32) NOT NULL,
        enabled       BOOLEAN DEFAULT TRUE,
        created_at    TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_module_permission (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES app_user(id),
        module_code VARCHAR(64) NOT NULL,
        UNIQUE(user_id, module_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_audit_log (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT,
        module_code VARCHAR(64),
        action      VARCHAR(64) NOT NULL,
        question    TEXT,
        answer      TEXT,
        citations   JSONB,
        model       VARCHAR(64),
        latency_ms  INTEGER,
        token_in    INTEGER,
        token_out   INTEGER,
        status      VARCHAR(16) NOT NULL,
        trace_id    VARCHAR(64),
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_module_time ON app_audit_log(module_code, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON app_audit_log(created_at)",
    # 访客时效令牌（§2.1）：与 sqlite 版同构
    """
    CREATE TABLE IF NOT EXISTS app_guest_token (
        id             BIGSERIAL PRIMARY KEY,
        jti            VARCHAR(64) UNIQUE NOT NULL,
        note           VARCHAR(256) DEFAULT '',
        exp            BIGINT NOT NULL,
        max_calls      INTEGER NOT NULL DEFAULT 30,
        used_calls     INTEGER NOT NULL DEFAULT 0,
        allow_real_llm BOOLEAN NOT NULL DEFAULT FALSE,
        real_llm_max   INTEGER NOT NULL DEFAULT 10,
        real_llm_used  INTEGER NOT NULL DEFAULT 0,
        revoked        BOOLEAN NOT NULL DEFAULT FALSE,
        created_at     TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_guest_jti ON app_guest_token(jti)",
]


def ensure_platform_tables() -> None:
    """幂等建平台表（含 app_guest_token）。任何运行模式（DEMO/PROD）启动时都调用。"""
    conn = get_conn()
    try:
        for sql in (_DDL_PG if settings.DB_DRIVER == "postgres" else _DDL_SQLITE):
            conn.execute(sql)
        conn.commit()
        logger.info("platform tables ensured (%s)", settings.DB_DRIVER)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 种子用户
# ---------------------------------------------------------------------------
# 注意：下列固定口令仅用于本地 DEMO（DEMO_MODE=true，无鉴权）。
# 生产模式首启会改为随机密码，只打印一次到后端启动日志，不写入代码/文档/前端。
DEMO_USERS = [
    # (username, password, role, display_name)
    ("admin", "Admin@123", "admin", "系统管理员"),
    ("analyst", "Analyst@123", "analyst", "工艺分析师"),
    ("viewer", "Viewer@123", "viewer", "一线查看"),
]


def _gen_initial_password() -> str:
    """生成 12 位初始密码（字母+数字，避免易混淆字符）。"""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def bootstrap_users() -> None:
    """app_user 为空时种入三角色账号。

    - DEMO 模式：使用固定开发口令（方便本地一键体验）
    - 生产模式：随机初始密码，仅打印到后端日志一次，登录后必须改密
    """
    from app.core.auth import hash_password  # 局部导入避免循环依赖

    conn = get_conn()
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM app_user")
        count = cur.fetchone()["c"]
        if count:
            return

        is_prod = settings.is_prod
        initials: List[tuple] = []
        for username, demo_pwd, role, display in DEMO_USERS:
            password = _gen_initial_password() if is_prod else demo_pwd
            initials.append((username, password, role, display))
            conn.execute(
                f"INSERT INTO app_user (username, display_name, password_hash, role, enabled) "
                f"VALUES ({ph()}, {ph()}, {ph()}, {ph()}, 1)",
                (username, display, hash_password(password), role),
            )
        conn.commit()

        if is_prod:
            lines = "\n".join(
                f"    {u} / {p}  ({r})" for u, p, r, _ in initials
            )
            logger.warning(
                "================ 初始账号（仅显示一次，请立即登录并改密）================\n%s\n"
                "======================================================================",
                lines,
            )
        else:
            logger.info(
                "bootstrapped DEMO users (admin/analyst/viewer) with local dev passwords"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 审计日志种子（管理页 14 天趋势演示；仅 sqlite，按天幂等补齐滚动窗口）
# ---------------------------------------------------------------------------
_SEED_MODULES = [
    "m04_sop_qa", "m01_equip_diagnosis", "m05_data_bi",
    "m03_quality_8d", "m02_tender_audit",
]
_SEED_QUESTIONS = {
    "m04_sop_qa": "注塑出现银纹应该怎么处理？",
    "m01_equip_diagnosis": "CNC-07 主轴振动超标如何排查？",
    "m05_data_bi": "9月11日良品率为什么下降？",
    "m03_quality_8d": "注塑件气泡缺陷 8D 分析",
    "m02_tender_audit": "招标文件资质条款审核",
}
_SEED_USERS = [1, 1, 2, 2, 3]  # admin/admin/analyst/analyst/viewer


def seed_audit_logs(days: int = 14) -> None:
    """为最近 days 天补齐演示审计数据（管理页趋势图有连续分布）。

    - 仅 sqlite（生产 pg 由真实流量填充，避免时区歧义）
    - 按天幂等：当天已有 seed 记录则跳过；重启自动补齐滚动窗口内缺失日期
    - 时间戳为 UTC（与 CURRENT_TIMESTAMP 一致），工作时段 9:00-18:00
    """
    if settings.DB_DRIVER != "sqlite":
        return
    import datetime as dt
    import random

    conn = get_conn()
    try:
        inserted_total = 0
        today = dt.datetime.now(dt.timezone.utc).date()
        for d_back in range(days - 1, -1, -1):
            day = today - dt.timedelta(days=d_back)
            day_str = day.strftime("%Y-%m-%d")
            cur = conn.execute(
                f"SELECT COUNT(*) AS c FROM app_audit_log "
                f"WHERE substr(created_at,1,10)={ph()} AND trace_id LIKE {ph()}",
                (day_str, "seed-%"),
            )
            if cur.fetchone()["c"]:
                continue
            # 确定性伪随机：同一种子日数据稳定（幂等双保险）
            rng = random.Random(day.toordinal())
            n = rng.randint(3, 8)
            for i in range(n):
                module = rng.choice(_SEED_MODULES)
                # 约 12% degraded、6% failed，其余 success
                roll = rng.random()
                status = "failed" if roll < 0.06 else ("degraded" if roll < 0.18 else "success")
                latency = rng.randint(650, 4200)
                hour = rng.randint(9, 17)
                minute = rng.randint(0, 59)
                ts = f"{day_str} {hour:02d}:{minute:02d}:{rng.randint(0, 59):02d}"
                uid = rng.choice(_SEED_USERS)
                q = _SEED_QUESTIONS[module]
                answer = "（演示种子数据）" if status != "success" else "（演示种子数据）分析完成"
                conn.execute(
                    f"INSERT INTO app_audit_log "
                    f"(user_id, module_code, action, question, answer, latency_ms, "
                    f" status, trace_id, created_at) "
                    f"VALUES ({ph()},{ph()},'invoke',{ph()},{ph()},{ph()},{ph()},{ph()},{ph()})",
                    (uid, module, q, answer, latency, status,
                     f"seed-{day_str}-{i}", ts),
                )
                inserted_total += 1
        if inserted_total:
            conn.commit()
            logger.info("audit seed logs inserted: %d (window=%dd)", inserted_total, days)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 模块清单同步（registry.scan 之后调用）
# ---------------------------------------------------------------------------
def sync_modules(manifests: List[Dict[str, Any]]) -> None:
    """把 registry 中的 manifest upsert 进 app_module（enabled 默认 1）。"""
    conn = get_conn()
    try:
        for m in manifests:
            conn.execute(
                f"UPDATE app_module SET name={ph()}, category={ph()}, version={ph()}, "
                f"manifest={ph()}, enabled=1, updated_at=CURRENT_TIMESTAMP WHERE code={ph()}",
                (m["name"], m.get("category", ""), m.get("version", "0.0.0"),
                 json.dumps(m, ensure_ascii=False), m["code"]),
            )
            cur = conn.execute(f"SELECT 1 FROM app_module WHERE code={ph()}", (m["code"],))
            if cur.fetchone() is None:
                conn.execute(
                    f"INSERT INTO app_module (code, name, category, version, manifest, enabled) "
                    f"VALUES ({ph()}, {ph()}, {ph()}, {ph()}, {ph()}, 1)",
                    (m["code"], m["name"], m.get("category", ""), m.get("version", "0.0.0"),
                     json.dumps(m, ensure_ascii=False)),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 模块级权限（viewer 白名单语义）
# ---------------------------------------------------------------------------
def get_user_permissions(user_id: int) -> List[str]:
    """返回用户被授权的 module_code 列表（viewer 白名单）。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            f"SELECT module_code FROM app_module_permission WHERE user_id={ph()}",
            (user_id,),
        )
        return [r["module_code"] for r in cur.fetchall()]
    finally:
        conn.close()


def grant_module(user_id: int, module_code: str) -> None:
    conn = get_conn()
    try:
        cur = conn.execute(
            f"SELECT 1 FROM app_module_permission WHERE user_id={ph()} AND module_code={ph()}",
            (user_id, module_code),
        )
        if cur.fetchone() is None:
            conn.execute(
                f"INSERT INTO app_module_permission (user_id, module_code) VALUES ({ph()}, {ph()})",
                (user_id, module_code),
            )
            conn.commit()
    finally:
        conn.close()


def revoke_module(user_id: int, module_code: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            f"DELETE FROM app_module_permission WHERE user_id={ph()} AND module_code={ph()}",
            (user_id, module_code),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.execute(
            f"SELECT * FROM app_user WHERE username={ph()}",
            (username,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.execute(f"SELECT * FROM app_user WHERE id={ph()}", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
