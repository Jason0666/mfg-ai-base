"""vLLM Adapter（M3 接入，M1 占位）。

vLLM 兼容 OpenAI 接口，可由 openai_compat_adapter 承载。
"""
from __future__ import annotations


class VllmAdapter:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        raise NotImplementedError("VllmAdapter will be implemented in M3")
