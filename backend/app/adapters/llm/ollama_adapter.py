"""Ollama Adapter（M3 接入，M1 占位）。

Ollama 自身即兼容 OpenAI 接口，可由 openai_compat_adapter 承载；
本类仅用于显式 driver 类型分发。
"""
from __future__ import annotations


class OllamaAdapter:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        raise NotImplementedError("OllamaAdapter will be implemented in M3")
