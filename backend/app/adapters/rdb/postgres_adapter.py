"""PostgreSQL Adapter（基于 SQLAlchemy 2.x）。

约束（C2/C3）：只提供只读能力，不提供 execute/write/DDL。
DEMO 模式下若 DB_DRIVER=sqlite，自动用 sqlite 引擎跑通骨架。
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.adapters.base import RelationalDB

logger = logging.getLogger(__name__)

# SQL 安全护栏：禁止任何写操作（C2 + §9.5 NL2SQL 安全校验）
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call)\b",
    re.IGNORECASE,
)


class PostgresAdapter(RelationalDB):
    def __init__(self, url: str, driver: str = "postgres") -> None:
        self._url = url
        self._driver = driver
        # sqlite 时打开 check_same_thread=False，避免多线程报错
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self._engine: Engine = create_engine(url, connect_args=connect_args, future=True)
        logger.info("PostgresAdapter engine ready: %s", self._safe_url(url))

    @staticmethod
    def _safe_url(url: str) -> str:
        # 隐藏密码
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)

    def query(self, sql: str, params: Optional[dict] = None) -> List[dict]:
        self._assert_readonly(sql)
        with self._engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result.fetchall()]

    def get_schema(self, tables: Optional[List[str]] = None) -> dict:
        insp = inspect(self._engine)
        names = tables or insp.get_table_names()
        schema: dict = {}
        for t in names:
            cols = insp.get_columns(t)
            schema[t] = [
                {"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable", True)}
                for c in cols
            ]
        return schema

    @staticmethod
    def _assert_readonly(sql: str) -> None:
        if _FORBIDDEN_KEYWORDS.search(sql):
            raise ValueError(f"non-SELECT SQL rejected by adapter: {sql[:80]}...")
