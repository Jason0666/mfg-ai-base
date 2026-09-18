"""NL2SQL 公共能力（§9.5 M05 数据问答）。

职责（P4：能力层复用，模块只做编排）：
1. SQL 安全校验（validate_sql）：仅 SELECT / 禁止危险字符与 DDL/DML / 表白名单
2. 自动补 LIMIT（ensure_limit）：上限由 settings.NL2SQL_MAX_ROWS 控制（默认 1000）
3. 表结构描述（describe_schema）：把 DB schema 渲染为 LLM 可读的建表上下文

SQL 生成本身由模块通过 llm_gateway + 外置 prompts 完成（P8：提示词配置驱动），
本模块不直接调 LLM。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# §9.5 安全护栏
_FORBIDDEN_TOKENS = re.compile(
    r"(;|--|/\*|\*/|\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|exec|execute|attach|pragma)\b)",
    re.IGNORECASE,
)

_LIMIT_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


def validate_sql(sql: str, table_whitelist: set) -> None:
    """校验 SQL 安全性，违规则抛 ValueError。

    强制约束（§9.5）：
    1. 仅允许 SELECT 开头
    2. 禁止 ;、--、/*、*/ 与 DDL/DML 关键字
    3. 表名（FROM/JOIN）必须在白名单内
    """
    if not sql or not sql.strip():
        raise ValueError("empty SQL")
    if _FORBIDDEN_TOKENS.search(sql):
        raise ValueError(f"unsafe SQL rejected: {sql[:80]}")
    if not re.match(r"^\s*select\b", sql, re.IGNORECASE):
        raise ValueError("only SELECT is allowed")
    # 提取并校验所有表名（FROM / JOIN，含子查询）
    tables = re.findall(r"\bfrom\s+([\"'`]?\w+[\"'`]?)", sql, re.IGNORECASE)
    tables += re.findall(r"\bjoin\s+([\"'`]?\w+[\"'`]?)", sql, re.IGNORECASE)
    for t in tables:
        t = t.strip("\"'`")
        if t not in table_whitelist:
            raise ValueError(f"table not in whitelist: {t}")


def ensure_limit(sql: str, max_rows: int = 1000) -> str:
    """自动补 LIMIT（上限 max_rows）。

    - 已有 LIMIT：原样返回（信任 LLM 给的限制，安全校验已保证 SELECT）
    - 无 LIMIT：末尾追加 LIMIT max_rows
    - 尾部多余分号已被 validate_sql 拒绝
    """
    if _LIMIT_RE.search(sql):
        return sql.strip()
    return f"{sql.strip()} LIMIT {max_rows}"


def describe_schema(schema: Dict[str, List[dict]]) -> str:
    """把 get_schema() 返回的表结构渲染为 LLM 可读的 DDL 风格描述。

    输入：{"table": [{"name": "date", "type": "DATE", "nullable": True}, ...]}
    输出：多行表结构文本。
    """
    lines: List[str] = []
    for table, cols in schema.items():
        lines.append(f"表名：{table}")
        for c in cols:
            nullable = "NULL" if c.get("nullable", True) else "NOT NULL"
            lines.append(f"  - {c.get('name', '')} ({c.get('type', 'TEXT')}, {nullable})")
        lines.append("")
    return "\n".join(lines).strip()


def extract_sql(raw: str) -> Optional[str]:
    """从 LLM 输出中提取 SQL（容错：去 markdown 代码块、去前后解释文字）。"""
    if not raw:
        return None
    t = raw.strip()
    # ```sql ... ``` 包裹
    m = re.search(r"```(?:sql)?\s*(.*?)\s*```", t, re.IGNORECASE | re.DOTALL)
    if m:
        t = m.group(1).strip()
    else:
        # 找第一个 SELECT 到语句末尾
        m = re.search(r"(select\b.*)", t, re.IGNORECASE | re.DOTALL)
        if m:
            t = m.group(1).strip()
    # 去掉末尾可能残留的解释文字（取到最后一个分号前；无分号则整体）
    if ";" in t:
        t = t.split(";")[0].strip()
    return t or None
