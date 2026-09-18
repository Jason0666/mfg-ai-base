"""UI schema 校验（§8.1 input_schema/output_schema）。

M1 职责：
- 校验 manifest 里的 input_schema/output_schema 是合法 JSON Schema 子集
- 提供 infer_widget 等辅助函数，给前端 SchemaInput 渲染提供 widget 默认值
"""
from __future__ import annotations

from typing import Any, Dict, List

WIDGET_TYPES = {"text", "textarea", "select", "number", "file", "date", "object"}


def validate_input_schema(schema: Dict[str, Any]) -> List[str]:
    """校验 input_schema，返回错误信息列表（空=通过）。"""
    errs: List[str] = []
    if not schema:
        return ["input_schema 为空"]
    if schema.get("type") not in {"object", None}:
        errs.append("input_schema.type 必须为 object")
    props = schema.get("properties")
    if not isinstance(props, dict):
        errs.append("input_schema.properties 必须是对象")
    return errs


def validate_output_schema(schema: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not schema:
        return errs  # output 可空
    if schema.get("type") not in {"object", None}:
        errs.append("output_schema.type 必须为 object")
    return errs


def infer_widget(prop: Dict[str, Any]) -> str:
    """根据 property 推断 widget 类型。"""
    if "widget" in prop:
        return prop["widget"]
    t = prop.get("type")
    if t == "string":
        return "text"
    if t == "integer" or t == "number":
        return "number"
    if t == "object":
        return "object"
    return "text"


def default_value(prop: Dict[str, Any]) -> Any:
    """根据 schema 生成默认值（前端表单初始化用）。"""
    if "default" in prop:
        return prop["default"]
    t = prop.get("type")
    if t == "string":
        return ""
    if t in {"integer", "number"}:
        return 0
    if t == "object":
        return {}
    if t == "array":
        return []
    return ""
