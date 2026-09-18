"""重排序（M2 简单实现）。

策略：
- 不引入 bge-reranker 本地模型（M3 再上）
- 实现「BM25 分数 + 条款号命中加分 + 版本活性加权」的轻量重排
- 输入：检索结果（list[dict]），输出：重排后的 top_n
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def rerank(query: str, docs: List[Dict[str, Any]], top_n: int = 5) -> List[Dict[str, Any]]:
    """简单重排：在 BM25 基础上叠加条款号命中与版本活性加分。

    加权项：
    - BM25 原始分数（归一化到 0-1）
    - 条款号在 query 中出现：+0.15
    - 文档名为 active 状态：+0.10；deprecated：-0.20
    - query 出现「现行版本/最新」等版本关键词：active +0.20
    """
    if not docs:
        return []

    # BM25 归一化
    max_score = max((d.get("score", 0.0) for d in docs), default=1.0) or 1.0
    q_lower = query.lower()

    has_version_keyword = any(
        kw in query
        for kw in ("现行", "最新", "现在", "active", "生效", "有效")
    )
    has_deprecated_keyword = any(
        kw in query for kw in ("废止", "旧版", "无效", "deprecated")
    )

    scored: List[tuple[Dict[str, Any], float]] = []
    for d in docs:
        base = d.get("score", 0.0) / max_score
        bonus = 0.0
        clause = str(d.get("clause", ""))
        # 条款号命中 query
        if clause and clause in query:
            bonus += 0.15
        # 版本加权
        status = str(d.get("status", "active")).lower()
        if status == "active":
            bonus += 0.10
            if has_version_keyword:
                bonus += 0.20
        elif status == "deprecated":
            bonus -= 0.20
            if has_deprecated_keyword:
                bonus += 0.30  # 用户明确问废止版时，废止版排前
        # 文档名命中 query（粗粒度相关）
        doc_name = str(d.get("doc", ""))
        if doc_name and any(ch in query for ch in doc_name[:6]):
            bonus += 0.05
        final = base + bonus
        d2 = dict(d)
        d2["rerank_score"] = round(final, 4)
        scored.append((d2, final))

    scored.sort(key=lambda kv: -kv[1])
    return [d for d, _ in scored[:top_n]]
