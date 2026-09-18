"""LLM 统一网关（§5.4）。

职责：
- 统一封装模型调用，提供降级、重试、限流、审计
- chat / chat_stream / structured 三种入口
- 捕获超时/限流/网络错误 → 重试 2 次（指数退避）→ 仍失败触发降级（P6）
- 每次调用写入审计（模块、耗时、token 数、成功与否）
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.adapters.factory import get_llm_provider
from app.config import settings
from app.core import audit

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    """LLM 统一网关单例。"""

    def __init__(self) -> None:
        self._provider = get_llm_provider()

    # ===== 非流式 =====
    def chat(
        self,
        messages: list[dict],
        *,
        module_code: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        user_id: Optional[int] = None,
    ) -> str:
        trace_id = audit.new_trace_id()
        start = audit.now_ms()
        last_err: Optional[Exception] = None
        answer = ""
        status = "success"

        for attempt in range(settings.LLM_MAX_RETRY + 1):
            try:
                answer = self._provider.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if not settings.llm_ready:
                    status = "degraded"
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < settings.LLM_MAX_RETRY:
                    backoff = 2 ** attempt
                    logger.warning(
                        "LLM chat attempt %d failed: %s, retry after %ds",
                        attempt + 1, e, backoff,
                    )
                    time.sleep(backoff)

        if last_err is not None:
            # 降级：不编造，返回明确提示（P6）
            answer = (
                f"【降级】模型调用失败：{last_err}。"
                "请检查 LLM_API_KEY/LLM_BASE_URL 配置后重试。"
            )
            status = "failed"

        latency = audit.now_ms() - start
        audit.audit_log(
            user_id=user_id,
            module_code=module_code,
            action="llm_chat",
            question=json.dumps(messages, ensure_ascii=False)[:4000],
            answer=answer,
            model=settings.LLM_MODEL,
            latency_ms=latency,
            status=status,
            trace_id=trace_id,
        )
        return answer

    # ===== 流式 =====
    def chat_stream(
        self,
        messages: list[dict],
        *,
        module_code: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        user_id: Optional[int] = None,
    ) -> Iterator[str]:
        trace_id = audit.new_trace_id()
        start = audit.now_ms()
        collected: list[str] = []
        status = "success" if settings.llm_ready else "degraded"

        try:
            for chunk in self._provider.chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                collected.append(chunk)
                yield chunk
        except Exception as e:  # noqa: BLE001
            status = "failed"
            msg = f"【降级】流式调用失败：{e}"
            collected.append(msg)
            yield msg

        latency = audit.now_ms() - start
        audit.audit_log(
            user_id=user_id,
            module_code=module_code,
            action="llm_stream",
            question=json.dumps(messages, ensure_ascii=False)[:4000],
            answer="".join(collected),
            model=settings.LLM_MODEL,
            latency_ms=latency,
            status=status,
            trace_id=trace_id,
        )

    # ===== 结构化输出 =====
    def structured(
        self,
        messages: list[dict],
        *,
        schema: Type[T],
        module_code: str,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        user_id: Optional[int] = None,
    ) -> T:
        """生成结构化输出，解析失败自动重试一次（追加 JSON 格式强调）。"""
        trace_id = audit.new_trace_id()
        start = audit.now_ms()
        status = "success"

        try:
            raw = self._provider.chat(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            try:
                return _parse_structured(raw, schema)
            except (ValidationError, ValueError) as e:
                logger.warning("structured parse failed, retry once: %s", e)
                retry_messages = messages + [
                    {
                        "role": "system",
                        "content": (
                            "请严格输出符合 schema 的 JSON，"
                            "不要包裹 markdown 代码块、不要任何解释文字。"
                        ),
                    }
                ]
                raw = self._provider.chat(
                    retry_messages, temperature=temperature, max_tokens=max_tokens
                )
                return _parse_structured(raw, schema)
        except Exception as e:  # noqa: BLE001
            status = "failed"
            raise

        finally:
            latency = audit.now_ms() - start
            audit.audit_log(
                user_id=user_id,
                module_code=module_code,
                action="llm_structured",
                model=settings.LLM_MODEL,
                latency_ms=latency,
                status=status,
                trace_id=trace_id,
            )


def _parse_structured(raw: str, schema: Type[T]) -> T:
    """容错解析：去掉 markdown 代码块、提取 JSON。"""
    s = raw.strip()
    if s.startswith("```"):
        # 去掉首行 ``` 和末行 ```
        lines = s.split("\n")
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        s = "\n".join(lines)
    # 尝试找最外层 { ... }
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    obj = json.loads(s)
    return schema.model_validate(obj)


# 单例
_gateway: Optional[LLMGateway] = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
