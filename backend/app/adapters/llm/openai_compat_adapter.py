"""OpenAI 兼容协议 Adapter（/v1/chat/completions + /v1/embeddings）。

适用：DeepSeek / 通义千问 / 智谱 / Ollama OpenAI 接口 / vLLM。
不使用 LangChain（A1）；直接 httpx 调用。

DEMO 模式或未配置 API Key：
- chat/chat_stream 返回占位文本（不真实调 LLM）
- embed 返回归一化的哈希向量（便于 DEMO 跑通，非真实向量）
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Iterator, List, Optional

import httpx

from app.adapters.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatAdapter(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        embed_base_url: str = "",
        embed_api_key: str = "",
        embed_model: str = "bge-m3",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.embed_base_url = (embed_base_url or base_url).rstrip("/")
        self.embed_api_key = embed_api_key or api_key
        self.embed_model = embed_model

    @property
    def _ready(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    # ===== chat =====

    def chat(self, messages: List[dict], **kwargs) -> str:
        if not self._ready:
            return self._placeholder_chat(messages)
        try:
            # trust_env=False 避免系统代理拦截 SSL（本机 127.0.0.1:10090 代理对 DeepSeek 不友好）
            with httpx.Client(trust_env=False, timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._auth_headers(),
                    json={
                        "model": kwargs.get("model", self.model),
                        "messages": messages,
                        "temperature": kwargs.get("temperature", 0.3),
                        "max_tokens": kwargs.get("max_tokens", 2000),
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM chat failed: %s", e)
            return self._placeholder_chat(messages, fallback=True)

    def chat_stream(self, messages: List[dict], **kwargs) -> Iterator[str]:
        if not self._ready:
            yield from self._placeholder_stream(messages)
            return
        try:
            # trust_env=False 避免系统代理拦截 SSL
            with httpx.Client(trust_env=False, timeout=self.timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._auth_headers(),
                    json={
                        "model": kwargs.get("model", self.model),
                        "messages": messages,
                        "temperature": kwargs.get("temperature", 0.3),
                        "max_tokens": kwargs.get("max_tokens", 2000),
                        "stream": True,
                    },
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = __import__("json").loads(payload)
                            delta = chunk["choices"][0].get("delta", {}).get("content")
                            if delta:
                                yield delta
                        except Exception:  # noqa: BLE001
                            continue
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM stream failed, fallback to placeholder: %s", e)
            yield from self._placeholder_stream(messages)

    # ===== embed =====

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._ready:
            return [self._hash_embedding(t, self.embed_dim) for t in texts]
        try:
            with httpx.Client(trust_env=False, timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.embed_base_url}/embeddings",
                    headers=self._auth_headers(self.embed_api_key),
                    json={"model": self.embed_model, "input": texts},
                )
                resp.raise_for_status()
                return [d["embedding"] for d in resp.json()["data"]]
        except Exception as e:  # noqa: BLE001
            logger.warning("embed failed, fallback to hash embedding: %s", e)
            return [self._hash_embedding(t, self.embed_dim) for t in texts]

    # ===== 占位实现（DEMO / 无 Key 时使用）=====

    @property
    def embed_dim(self) -> int:
        # 与 config.VECTOR_DIM 默认保持一致
        return 1024

    def _placeholder_chat(self, messages: List[dict], fallback: bool = False) -> str:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        tag = "降级" if fallback else "演示模式"
        return (
            f"【{tag}】未配置真实 LLM，无法对「{last_user[:40]}」做真实推理。"
            "请在 .env 中配置 LLM_API_KEY 以启用真实推理；"
            "或保持 DEMO_MODE=true 并使用模块内置演示剧本。"
        )

    def _placeholder_stream(self, messages: List[dict]) -> Iterator[str]:
        text = self._placeholder_chat(messages)
        # 模拟打字机：每 3-5 字一片
        i = 0
        while i < len(text):
            yield text[i : i + 4]
            i += 4
            time.sleep(0.02)

    @staticmethod
    def _hash_embedding(text: str, dim: int) -> List[float]:
        """基于 MD5 的伪向量（仅 DEMO 占位，不用于真实检索）。"""
        out = []
        for i in range(dim):
            h = hashlib.md5(f"{text}|{i}".encode("utf-8")).digest()
            # 取前 4 字节为 int32，归一化到 [-1, 1]
            n = int.from_bytes(h[:4], "big", signed=False) / 0x7FFFFFFF
            out.append(n - 1.0)
        # 归一化
        norm = sum(x * x for x in out) ** 0.5 or 1e-9
        return [x / norm for x in out]

    def _auth_headers(self, api_key: Optional[str] = None) -> dict:
        key = api_key or self.api_key
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
