"""pgvector Adapter。

M1 行为：
- 若底层数据库可用且为 postgres+pgvector，使用真实向量检索
- DEMO 模式 / sqlite 环境，退化为内存版向量库（按余弦相似度计算）
  这样可以保证 docker compose up 不强依赖 pgvector 扩展安装
- M2 接入真实 RAG 链路时，会切换为真实 pgvector 表
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import List, Optional

from app.adapters.base import VectorStore

logger = logging.getLogger(__name__)


class _InMemoryCollection:
    """内存向量集合，余弦相似度。仅 DEMO 用。"""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.ids: List[str] = []
        self.vectors: List[List[float]] = []
        self.metadatas: List[dict] = []

    def upsert(self, ids: List[str], vectors: List[List[float]], metadatas: List[dict]) -> None:
        for i, vid, vec, meta in zip(range(len(ids)), ids, vectors, metadatas):
            if vid in self.ids:
                idx = self.ids.index(vid)
                self.vectors[idx] = vec
                self.metadatas[idx] = meta
            else:
                self.ids.append(vid)
                self.vectors.append(vec)
                self.metadatas.append(meta)

    def search(self, vector: List[float], top_k: int, filters: Optional[dict]) -> List[dict]:
        if not self.vectors:
            return []
        sims = []
        for vid, vec, meta in zip(self.ids, self.vectors, self.metadatas):
            if filters and not all(meta.get(k) == v for k, v in filters.items()):
                continue
            sims.append((self._cosine(vector, vec), vid, meta))
        sims.sort(reverse=True)
        return [
            {"id": vid, "score": s, "metadata": meta}
            for s, vid, meta in sims[:top_k]
        ]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(x * x for x in b)) or 1e-9
        return sum(x * y for x, y in zip(a, b)) / (na * nb)


class PgvectorAdapter(VectorStore):
    """pgvector 向量库适配器。

    M1 阶段使用内存退化实现；M2 阶段会接入真实 pgvector 表。
    """

    def __init__(self, driver: str = "pgvector", dim: int = 1024) -> None:
        self.driver = driver
        self.dim = dim
        self._collections: dict[str, _InMemoryCollection] = {}
        logger.info("PgvectorAdapter ready (M1 in-memory mode, dim=%d)", dim)

    def _get(self, collection: str) -> _InMemoryCollection:
        if collection not in self._collections:
            self._collections[collection] = _InMemoryCollection(self.dim)
        return self._collections[collection]

    def upsert(
        self,
        collection: str,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: List[dict],
    ) -> None:
        self._get(collection).upsert(ids, vectors, metadatas)

    def search(
        self,
        collection: str,
        vector: List[float],
        top_k: int = 8,
        filters: Optional[dict] = None,
    ) -> List[dict]:
        return self._get(collection).search(vector, top_k, filters)

    def delete(self, collection: str, ids: List[str]) -> None:
        coll = self._collections.get(collection)
        if not coll:
            return
        for vid in ids:
            if vid in coll.ids:
                idx = coll.ids.index(vid)
                coll.ids.pop(idx)
                coll.vectors.pop(idx)
                coll.metadatas.pop(idx)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex
