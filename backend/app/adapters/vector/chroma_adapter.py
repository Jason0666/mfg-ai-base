"""Chroma 向量库 Adapter —— 架构预留接口，当前版本【尚未实现】。

注意：本文件仅占位，不代表系统当前支持 Chroma。工厂层遇到 VECTOR_DRIVER=chroma
会直接抛出明确错误。本地 DEMO 请使用 pgvector 的内存退化模式（默认配置）。
"""
from __future__ import annotations


class ChromaAdapter:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "Chroma 适配器为架构预留，当前版本尚未实现；如需使用请联系交付团队。"
        )
