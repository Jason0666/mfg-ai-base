"""Chroma 向量库 Adapter（M3 接入，M1 仅占位）。

DEMO 阶段使用 pgvector_adapter 的内存退化模式即可。
"""
from __future__ import annotations


class ChromaAdapter:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        raise NotImplementedError("ChromaAdapter will be implemented in M3")
