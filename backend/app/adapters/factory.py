"""数据接入层工厂（§5.2）。

业务代码只调用 get_vector_store() / get_relational_db() / get_file_store() /
get_llm_provider()，不感知具体实现。切换数据源只改 .env。

当前版本已实现的 driver：
- 关系库：postgres（psycopg2）、sqlite（DEMO/单机，复用同一适配器）
- 向量库：pgvector（DEMO 下退化为内存版）
- 文件存储：local（本地磁盘）
- LLM：openai_compat

架构预留但【尚未实现】的 driver：mysql / dm（达梦）/ kingbase（金仓）/
chroma / milvus / minio / smb —— 这些仅为接口占位，不代表当前版本已支持；
客户现场若选用，须先完成适配器开发与联调，切勿在未验证环境配置为生产。
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

# 架构预留、当前版本尚未实现的 driver（对外统一措辞，避免被误解为已支持）
_RDB_RESERVED = {"mysql", "dm", "kingbase"}
_VECTOR_RESERVED = {"chroma", "milvus"}
_FILE_RESERVED = {"smb", "minio"}


def _reserved_error(layer: str, driver: str) -> NotImplementedError:
    return NotImplementedError(
        f"{layer} driver='{driver}' 为架构预留接口，当前版本尚未实现（不代表已支持）。"
        f"如需在客户现场使用，请先联系交付团队完成该适配器的开发与联调，"
        f"切勿在未验证的环境中配置为生产。"
    )


@lru_cache
def get_relational_db() -> RelationalDB:
    """按 DB_DRIVER 返回关系库实现。

    已实现：postgres / sqlite；mysql / dm / kingbase 为架构预留，显式报错。
    """
    driver = settings.DB_DRIVER.lower()
    if driver in _RDB_RESERVED:
        raise _reserved_error("关系数据库", driver)

    from app.adapters.rdb.postgres_adapter import PostgresAdapter

    db = PostgresAdapter(url=settings.db_url, driver=settings.DB_DRIVER)
    logger.info("RelationalDB initialized: driver=%s", settings.DB_DRIVER)
    return db


@lru_cache
def get_vector_store() -> VectorStore:
    """按 VECTOR_DRIVER 返回向量库实现。

    已实现：pgvector（DEMO/sqlite 下退化为内存版）；chroma / milvus 为架构预留。
    """
    driver = settings.VECTOR_DRIVER.lower()
    if driver in _VECTOR_RESERVED:
        raise _reserved_error("向量库", driver)

    from app.adapters.vector.pgvector_adapter import PgvectorAdapter

    store = PgvectorAdapter(driver=settings.VECTOR_DRIVER, dim=settings.VECTOR_DIM)
    logger.info("VectorStore initialized: driver=%s dim=%d", settings.VECTOR_DRIVER, settings.VECTOR_DIM)
    return store


@lru_cache
def get_file_store() -> FileStore:
    """按 FILE_DRIVER 返回文件存储实现。

    已实现：local；smb / minio 为架构预留，显式报错。
    """
    driver = settings.FILE_DRIVER.lower()
    if driver in _FILE_RESERVED:
        raise _reserved_error("文件存储", driver)

    from app.adapters.files.local_adapter import LocalFileStore

    store = LocalFileStore(root=settings.FILE_ROOT)
    logger.info("FileStore initialized: driver=local root=%s", settings.FILE_ROOT)
    return store


@lru_cache
def get_llm_provider() -> LLMProvider:
    """按 LLM_DRIVER 返回 LLM 实现。当前支持 openai_compat；ollama/vllm 为预留。"""
    driver = settings.LLM_DRIVER.lower()
    if driver == "openai_compat":
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
    elif driver in ("ollama", "vllm"):
        raise _reserved_error("LLM", driver)
    else:
        raise ValueError(f"未知 LLM_DRIVER: {settings.LLM_DRIVER}（支持：openai_compat）")
    logger.info(
        "LLMProvider initialized: driver=openai_compat model=%s ready=%s",
        settings.LLM_MODEL,
        settings.llm_ready,
    )
    return provider
