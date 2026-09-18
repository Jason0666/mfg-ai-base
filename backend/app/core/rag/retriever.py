"""混合检索（向量 + 关键词，M2 BM25 真实实现）。

§9.4 M04 SOP：口语 query 改写 → 混合检索 → 版本过滤 → 重排 → 生成答案

M2 实现策略（按 project memory 既定方针）：
- 主检索：BM25（纯 Python 实现，不依赖外部库）
- 辅检索：hash 向量（当配置了 EMBED_DRIVER 时）
- 缓存：进程内 chunk 索引，按 collection key 懒加载

注：DeepSeek 无 /v1/embeddings 接口，M2 以 BM25 为主，hash 向量为辅（不调外部 embedding 服务）。
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.rag.chunker import Chunk, chunk_directory

logger = logging.getLogger(__name__)


# ===== 中文分词（极简版，不依赖 jieba） =====

# 停用词
_STOPWORDS = set("的了和与及或是在都有对为从被把让向以按照由于所以但是然而因此之一一个一些一种这这种那那些".split())
# 英文停用词
_STOPWORDS_EN = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "this", "that", "these", "those", "it", "its", "they", "them",
    "their", "there", "here", "what", "which", "who", "how", "when", "where",
}


def tokenize(text: str) -> List[str]:
    """极简中文分词：中文按字 + 二元词，英文按词。

    纯标准库实现，不引入 jieba 等外部依赖。
    效果：覆盖度高、精度够 BM25 排序使用。
    """
    if not text:
        return []
    tokens: List[str] = []
    # 英文/数字词
    for m in re.finditer(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", text):
        w = m.group(0).lower()
        if w not in _STOPWORDS_EN and len(w) > 1:
            tokens.append(w)
    # 中文按字 + 二元 bigram
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    # 单字（去停用词）
    for ch in chinese_chars:
        if ch not in _STOPWORDS:
            tokens.append(ch)
    # 二元
    for i in range(len(chinese_chars) - 1):
        bigram = chinese_chars[i] + chinese_chars[i + 1]
        if bigram not in _STOPWORDS:
            tokens.append(bigram)
    # 三元（关键术语命中加分）
    for i in range(len(chinese_chars) - 2):
        tokens.append(chinese_chars[i] + chinese_chars[i + 1] + chinese_chars[i + 2])
    return tokens


# ===== BM25 索引 =====


@dataclass
class BM25Index:
    """简单 BM25 索引（Okapi BM25 实现）。

    公式：score(q, d) = Σ_t∈q IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1*(1-b+b*|d|/avgdl))
    """
    docs: List[Chunk] = field(default_factory=list)
    doc_tokens: List[List[str]] = field(default_factory=list)
    df: Dict[str, int] = field(default_factory=dict)       # 文档频率
    tf: List[Dict[str, int]] = field(default_factory=list)  # 每篇文档的词频
    doc_len: List[int] = field(default_factory=list)
    avgdl: float = 0.0
    k1: float = 1.5
    b: float = 0.75
    N: int = 0

    def build(self, chunks: List[Chunk]) -> None:
        self.docs = chunks
        self.doc_tokens = []
        self.df = {}
        self.tf = []
        self.doc_len = []
        for c in chunks:
            toks = tokenize(c.content)
            self.doc_tokens.append(toks)
            self.doc_len.append(len(toks))
            tf_map: Dict[str, int] = {}
            for t in toks:
                tf_map[t] = tf_map.get(t, 0) + 1
            self.tf.append(tf_map)
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
        self.N = len(chunks)
        self.avgdl = (sum(self.doc_len) / self.N) if self.N > 0 else 0.0

    def search(self, query: str, top_k: int = 8) -> List[tuple[Chunk, float]]:
        if self.N == 0:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores: List[float] = [0.0] * self.N
        idf_cache: Dict[str, float] = {}
        for t in q_tokens:
            if t in idf_cache:
                idf = idf_cache[t]
            else:
                df = self.df.get(t, 0)
                # Okapi IDF（带平滑，避免负值）
                idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)
                idf_cache[t] = idf
            for i in range(self.N):
                tf = self.tf[i].get(t, 0)
                if tf == 0:
                    continue
                dl = self.doc_len[i] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[i] += idf * (tf * (self.k1 + 1)) / denom
        # 排序
        ranked = sorted(enumerate(scores), key=lambda kv: -kv[1])
        result: List[tuple[Chunk, float]] = []
        for idx, score in ranked[:top_k]:
            if score > 0:
                result.append((self.docs[idx], score))
        return result


# ===== 检索服务（带缓存） =====


class RetrievalService:
    """检索服务：按 collection 路径懒加载索引并检索。

    collection 约定：collection 名 = 模块 seed/knowledge/ 子目录名。
    M04 默认 collection: ["m04_sop_qa"]（指向 backend/app/modules/m04_sop_qa/seed/knowledge/）
    """

    def __init__(self) -> None:
        self._indexes: Dict[str, BM25Index] = {}
        self._chunks_cache: Dict[str, List[Chunk]] = {}

    def _collection_dir(self, collection: str) -> Optional[Path]:
        """定位 collection 对应的物理目录。

        约定：
        - 优先查找 backend/app/modules/{collection}/seed/knowledge/
        - 次选 backend/seed/knowledge/{collection}/
        """
        from app.config import settings
        base = Path(settings.BASE_DIR).resolve()
        # 优先模块内 seed
        mod_path = base / "app" / "modules" / collection / "seed" / "knowledge"
        if mod_path.exists():
            return mod_path
        # 备用：顶层 seed/knowledge/{collection}
        seed_path = base / "seed" / "knowledge" / collection
        if seed_path.exists():
            return seed_path
        # 最后：直接把 collection 当成路径
        p = Path(collection)
        if p.exists():
            return p
        return None

    def _ensure_index(self, collections: List[str]) -> BM25Index:
        """构建或取回合并索引（多个 collection 合并检索）。"""
        cache_key = "|".join(sorted(collections))
        if cache_key in self._indexes:
            return self._indexes[cache_key]
        all_chunks: List[Chunk] = []
        for c in collections:
            if c in self._chunks_cache:
                all_chunks.extend(self._chunks_cache[c])
                continue
            d = self._collection_dir(c)
            if d is None:
                logger.warning("collection dir not found: %s", c)
                continue
            chunks = chunk_directory(d)
            self._chunks_cache[c] = chunks
            all_chunks.extend(chunks)
        idx = BM25Index()
        idx.build(all_chunks)
        self._indexes[cache_key] = idx
        return idx

    def search(
        self,
        query: str,
        collections: List[str],
        top_k: int = 8,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """检索入口。返回 chunk dict 列表（含 score 字段）。

        filters 暂未使用，保留接口供 M3 接 pgvector 时按元数据过滤。
        """
        idx = self._ensure_index(collections)
        results = idx.search(query, top_k=top_k)
        out: List[Dict[str, Any]] = []
        for chunk, score in results:
            d = chunk.to_dict()
            d["score"] = float(round(score, 4))
            out.append(d)
        return out


# 单例
_service: Optional[RetrievalService] = None


def get_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service


# 兼容旧 API 签名
def search(query: str, collections: list[str], top_k: int = 8) -> list[dict]:
    """模块级便捷检索入口。"""
    return get_service().search(query, collections, top_k=top_k)


def clear_cache() -> None:
    """清空索引缓存（重新加载文档时调用）。"""
    global _service
    _service = None
