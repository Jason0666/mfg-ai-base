"""§2.2 平台级演示剧本匹配器。

职责（平台统一实现，不下放到各业务模块）：
1. 加载各模块 seed/demo_scripts.json 预设剧本；
2. 按输入文本做关键词模糊匹配（归一化去标点/空白、大小写不敏感、命中数计分）；
3. 判断"剧本模式"是否生效：DEMO_MODE=true 且未配置任何 LLM Key（force 供访客模式复用）；
4. 提供 50–120ms 随机间隔的模拟流式分片；
5. 未命中剧本时返回统一、明确的提示文案（不得静默失败或返回空）。

注意：真实链路（RAG / NL2SQL / 导出）不受本匹配器影响——剧本模式是新增分支。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_MODULES_ROOT = Path(__file__).resolve().parent.parent / "modules"

# 归一化时剔除的标点与空白（CNC-07 → CNC07，问答两侧同时归一化，保持可匹配）
_PUNCT_RE = re.compile(
    r"[\s\-_/\\，。．\.,、？?！!（）\(\)\[\]【】\"'“”‘’：:；;~～·]+"
)

# 进程内缓存（剧本文件极小；改动后重启后端生效）
_cache: Dict[str, List[dict]] = {}


def _normalize(text: str) -> str:
    """小写 + 去标点/空白，用于关键词模糊匹配。"""
    return _PUNCT_RE.sub("", str(text or "").lower())


def is_active(*, force: bool = False) -> bool:
    """剧本模式是否生效：演示模式且没有任何 LLM Key；force 供访客令牌等场景复用。"""
    return bool(force or (settings.DEMO_MODE and not settings.llm_ready))


def is_active_for_request(request: Any) -> bool:
    """按请求上下文判定剧本模式（§2.1 降级策略）。

    中间件对访客令牌写入 request.state.guest_force_script：
    普通访客恒为 True（强制剧本，零 LLM 成本）；高级访客在真实 LLM
    配额耗尽后转 True（自动降级）。非访客请求恒走默认判定。
    """
    force = bool(getattr(getattr(request, "state", None), "guest_force_script", False))
    return is_active(force=force)


def scripts_path(module_code: str) -> Path:
    return _MODULES_ROOT / module_code / "seed" / "demo_scripts.json"


def load_scripts(module_code: str) -> List[dict]:
    """读取模块剧本列表；文件缺失或解析失败返回空列表（不抛异常）。"""
    if module_code in _cache:
        return _cache[module_code]
    p = scripts_path(module_code)
    scripts: List[dict] = []
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                scripts = [s for s in data if isinstance(s, dict)]
        except Exception:  # noqa: BLE001
            logger.warning("[demo_script] bad json: %s", p)
    _cache[module_code] = scripts
    return scripts


def script_count(module_code: str) -> int:
    return len(load_scripts(module_code))


def match(module_code: str, text: str) -> Optional[dict]:
    """关键词模糊匹配：命中关键词数最多的剧本胜出；同分时关键词总字数更长者优先。

    只做包含匹配（归一化后子串），不做跨模块匹配。未命中返回 None。
    """
    scripts = load_scripts(module_code)
    norm_q = _normalize(text)
    if not scripts or not norm_q:
        return None

    best: Optional[dict] = None
    best_hits = 0
    best_kw_len = 0
    for s in scripts:
        kws = s.get("keywords") or []
        hits = 0
        kw_len = 0
        for kw in kws:
            nkw = _normalize(kw)
            if nkw and nkw in norm_q:
                hits += 1
                kw_len += len(nkw)
        if hits <= 0:
            continue
        if hits > best_hits or (hits == best_hits and kw_len > best_kw_len):
            best, best_hits, best_kw_len = s, hits, kw_len
    return best


def not_matched_message(module_code: str) -> str:
    """未命中剧本时的统一提示（明确、不静默、不返回空）。"""
    n = script_count(module_code)
    if n > 0:
        return (
            f"演示模式当前仅支持页面推荐的 {n} 个示例问题，"
            "请点击下方「推荐问题」体验完整演示效果；"
            "自由提问需要在系统配置大模型（LLM API Key）后使用。"
        )
    return (
        "演示模式暂未配置该模块的示例剧本，请联系管理员补充 seed/demo_scripts.json；"
        "自由提问需要在系统配置大模型（LLM API Key）后使用。"
    )


async def simulated_chunks(
    text: str,
    *,
    chunk_size: int = 12,
    min_delay: float = 0.05,
    max_delay: float = 0.12,
) -> AsyncIterator[str]:
    """模拟流式输出：按 chunk_size 分片，每片随机间隔 50–120ms。

    yield 的是纯文本片段，调用方自行包装为 SSE chunk 事件。
    """
    if not text:
        return
    i = 0
    while i < len(text):
        yield text[i : i + chunk_size]
        i += chunk_size
        await asyncio.sleep(random.uniform(min_delay, max_delay))
