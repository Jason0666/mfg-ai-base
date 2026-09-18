"""向量化（M2 实现，M1 占位）。

默认走 openai_compat（bge-m3 等）；离线场景使用 bge-small-zh 本地模型。
"""
from __future__ import annotations


def embed_texts(texts: list[str]) -> list[list[float]]:
    raise NotImplementedError("embedder.embed_texts will be implemented in M2")
