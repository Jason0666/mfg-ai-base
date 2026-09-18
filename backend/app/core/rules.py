"""通用规则引擎（M2/M3 接入，M1 占位）。

用途：标书审核（M02）的废标规则、质量审核等的硬性规则。
M1 不使用本模块。
"""
from __future__ import annotations


class RuleEngine:
    """M1 占位：仅记录接口契约，无实现。"""

    def evaluate(self, facts: dict, rule_set: str = "default") -> list[dict]:
        raise NotImplementedError("RuleEngine will be implemented in M2/M3")
