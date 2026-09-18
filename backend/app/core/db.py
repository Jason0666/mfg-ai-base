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
]


def ensure_platform_tables() -> None:
    """幂等建平台四表。任何运行模式（DEMO/PROD）启动时都调用。"""
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
DEFAULT_USERS = [
    # (username, password, role, display_name)
    ("admin", "Admin@123", "admin", "系统管理员"),
    ("analyst", "Analyst@123", "analyst", "工艺分析师"),
    ("viewer", "Viewer@123", "viewer", "一线查看"),
]


def bootstrap_users() -> None:
    """app_user 为空时种入三角色默认账号（首启打印警告提醒改密）。"""
    from app.core.auth import hash_password  # 局部导入避免循环依赖

    conn = get_conn()
    try:
        cur = conn.execute("SELECT COUNT(*) AS c FROM app_user")
        count = cur.fetchone()["c"]
        if count:
            return
        for username, password, role, display in DEFAULT_USERS:
            conn.execute(
                f"INSERT INTO app_user (username, display_name, password_hash, role, enabled) "
                f"VALUES ({ph()}, {ph()}, {ph()}, {ph()}, 1)",
                (username, display, hash_password(password), role),
            )
        conn.commit()
        logger.warning(
            "bootstrapped default users (admin/analyst/viewer) with default passwords — "
            "PLEASE CHANGE THEM IMMEDIATELY via SQL: UPDATE app_user SET password_hash=..."
        )
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
