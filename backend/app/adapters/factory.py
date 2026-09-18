"""数据接入层工厂（§5.2）。

业务代码只调用 get_vector_store() / get_relational_db() / get_file_store() /
get_llm_provider()，不感知具体实现。切换数据源只改 .env。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.adapters.base import (
    FileStore,
    LLMProvider,
    RelationalDB,
    VectorStore,
)

logger = logging.getLogger(__name__)


@lru_cache
def get_relational_db() -> RelationalDB:
    """按 DB_DRIVER 返回关系库实现。

    M1 仅提供 postgres 实现（基于 SQLAlchemy，亦兼容 sqlite 用于 DEMO）。
    M3 接入 mysql/dm/kingbase。
    """
    from app.adapters.rdb.postgres_adapter import PostgresAdapter

    db = PostgresAdapter(url=settings.db_url, driver=settings.DB_DRIVER)
    logger.info("RelationalDB initialized: driver=%s", settings.DB_DRIVER)
    return db


@lru_cache
def get_vector_store() -> VectorStore:
    """按 VECTOR_DRIVER 返回向量库实现。

    M1 提供 pgvector 实现（DEMO/sqlite 下退化为内存版，便于骨架跑通）。
    """
    from app.adapters.vector.pgvector_adapter import PgvectorAdapter

    store = PgvectorAdapter(driver=settings.VECTOR_DRIVER, dim=settings.VECTOR_DIM)
    logger.info("VectorStore initialized: driver=%s dim=%d", settings.VECTOR_DRIVER, settings.VECTOR_DIM)
    return store


@lru_cache
def get_file_store() -> FileStore:
    """按 FILE_DRIVER 返回文件存储实现。M1 仅 local。"""
    from app.adapters.files.local_adapter import LocalFileStore

    store = LocalFileStore(root=settings.FILE_ROOT)
    logger.info("FileStore initialized: driver=local root=%s", settings.FILE_ROOT)
    return store


@lru_cache
def get_llm_provider() -> LLMProvider:
    """按 LLM_DRIVER 返回 LLM 实现。M1 仅 openai_compat。"""
    from app.adapters.llm.openai_compat_adapter import OpenAICompatAdapter

    provider = OpenAICompatAdapter(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        timeout=settings.LLM_TIMEOUT,
        embed_base_url=settings.EMBED_BASE_URL or settings.LLM_BASE_URL,
        embed_api_key=settings.EMBED_API_KEY or settings.LLM_API_KEY,
        embed_model=settings.EMBED_MODEL,
    )
    logger.info(
        "LLMProvider initialized: driver=openai_compat model=%s ready=%s",
        settings.LLM_MODEL,
        settings.llm_ready,
    )
    return provider
