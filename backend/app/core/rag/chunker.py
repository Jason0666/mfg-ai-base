"""文档切片（M2 实现）。

策略：
- 解析带 YAML frontmatter 的 Markdown 文档（种子数据格式）
- 按 ## 二级标题切片，保留页码（按字符数估算）与条款号
- 每个切片携带 doc/version/effective_date/status/page/clause 元数据
- 不依赖外部库，纯标准库实现
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Chunk:
    """一个检索切片。"""
    doc: str                      # 文档名（如 SOP-PU-001 注塑机操作指导书）
    version: str                  # V3.2
    effective_date: str           # 2025-08-15
    status: str                   # active | deprecated
    deprecated_date: Optional[str]   # 废止日期（仅废止版有）
    replaced_by: Optional[str]       # 现行版本号（仅废止版有）
    page: int                     # 页码（估算）
    clause: str                   # 条款号（如 3.2.1 或小节标题）
    content: str                  # 切片正文
    source_file: str = ""         # 源文件名

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc": self.doc,
            "version": self.version,
            "effective_date": self.effective_date,
            "status": self.status,
            "deprecated_date": self.deprecated_date,
            "replaced_by": self.replaced_by,
            "page": self.page,
            "clause": self.clause,
            "content": self.content,
            "source_file": self.source_file,
        }


# 简单 frontmatter 解析（不引入 pyyaml 依赖到 chunker）
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
# 二级/三级标题
_HEAD_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
# 条款号提取（如 3.2.1 / 一、 / 1.1 等）
_CLAUSE_RE = re.compile(r"^(\s*)(\d+(?:\.\d+)*|第[一二三四五六七八九十]+条|第[一二三四五六七八九十]+章|[一二三四五六七八九十]+、)\s*(.*)$", re.MULTILINE)


def parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    """解析 YAML frontmatter，返回 (meta, body)。

    只做扁平 key:value 解析，足够种子文档使用；不处理嵌套结构。
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip().strip('"').strip("'")
            if v.lower() in ("null", "none", "~", ""):
                meta[k.strip()] = ""
            else:
                meta[k.strip()] = v
    return meta, m.group(2)


def _normalize_clause(heading: str) -> str:
    """从标题文本提取条款号；无法提取则返回原标题。"""
    m = re.match(r"^(\d+(?:\.\d+)*\.?)\s*(.*)$", heading)
    if m:
        clause = m.group(1).rstrip(".")
        return clause
    return heading


def chunk_markdown(text: str, source_file: str = "") -> List[Chunk]:
    """切片一份 Markdown 文档。

    策略：
    1. 解析 frontmatter，取元数据
    2. 按二级/三级标题切分，每段为一个 chunk
    3. 页码按累积字符数估算：每 800 字符约 1 页
    """
    meta, body = parse_frontmatter(text)
    if not meta:
        # 无 frontmatter，按 800 字符粗切
        return [
            Chunk(
                doc=source_file,
                version="",
                effective_date="",
                status="active",
                deprecated_date=None,
                replaced_by=None,
                page=1,
                clause="",
                content=body.strip(),
                source_file=source_file,
            )
        ]

    doc_name = meta.get("doc", source_file)
    version = meta.get("version", "")
    effective_date = meta.get("effective_date", "")
    status = meta.get("status", "active").lower()
    deprecated_date = meta.get("deprecated_date", "") or None
    replaced_by = meta.get("replaced_by", "") or None
    page_count = int(meta.get("page_count", "1") or "1")

    # 按标题切片
    chunks: List[Chunk] = []
    # 用 split 保留标题
    parts = re.split(r"^(#{2,3}\s+.+)$", body, flags=re.MULTILINE)
    # parts 形如 [前言, '## 标题1', 正文1, '## 标题2', 正文2, ...]
    preface = parts[0].strip() if parts else ""
    if preface:
        # 前言作为一个 chunk
        page = max(1, _estimate_page(preface, page_count, body))
        chunks.append(Chunk(
            doc=doc_name, version=version, effective_date=effective_date,
            status=status, deprecated_date=deprecated_date, replaced_by=replaced_by,
            page=page, clause="前言", content=preface, source_file=source_file,
        ))

    i = 1
    char_accum = len(preface)
    total_chars = len(body)
    while i < len(parts) - 1:
        heading_line = parts[i].strip()
        body_text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        # 提取标题文本
        m = re.match(r"^#{2,3}\s+(.+?)$", heading_line)
        clause_text = m.group(1).strip() if m else heading_line
        clause = _normalize_clause(clause_text)

        content = f"{heading_line}\n{body_text}".strip()
        if content:
            char_accum += len(body_text) + len(heading_line)
            page = _estimate_page_cumulative(char_accum, total_chars, page_count)
            chunks.append(Chunk(
                doc=doc_name, version=version, effective_date=effective_date,
                status=status, deprecated_date=deprecated_date, replaced_by=replaced_by,
                page=page, clause=clause, content=content, source_file=source_file,
            ))
        i += 2

    # 兜底：如果没切出任何 chunk，把整个 body 作为一个
    if not chunks:
        chunks.append(Chunk(
            doc=doc_name, version=version, effective_date=effective_date,
            status=status, deprecated_date=deprecated_date, replaced_by=replaced_by,
            page=1, clause="全文", content=body.strip(), source_file=source_file,
        ))

    return chunks


def _estimate_page(text: str, page_count: int, body: str) -> int:
    """按字符占比估算页码。"""
    total = len(body) or 1
    ratio = len(text) / total
    return max(1, min(page_count, int(ratio * page_count) + 1))


def _estimate_page_cumulative(char_accum: int, total_chars: int, page_count: int) -> int:
    """按累积字符位置估算页码。"""
    if total_chars <= 0:
        return 1
    ratio = char_accum / total_chars
    return max(1, min(page_count, int(ratio * page_count) + 1))


def chunk_file(path: Path) -> List[Chunk]:
    """从文件路径切片。"""
    text = path.read_text(encoding="utf-8")
    return chunk_markdown(text, source_file=path.name)


def chunk_directory(dir_path: Path) -> List[Chunk]:
    """递归扫描目录下所有 .md 文件并切片。"""
    all_chunks: List[Chunk] = []
    if not dir_path.exists():
        return all_chunks
    for p in sorted(dir_path.rglob("*.md")):
        try:
            all_chunks.extend(chunk_file(p))
        except Exception:  # noqa: BLE001
            continue
    return all_chunks


# 保留原 M1 占位接口名（向后兼容）
def chunk_text(text: str, max_tokens: int = 512, overlap: int = 50) -> list[str]:
    """简单字符切片（兼容旧 API，新代码请用 chunk_markdown）。"""
    chunks = []
    i = 0
    step = max_tokens
    while i < len(text):
        chunks.append(text[i:i + step])
        i += step - overlap
    return chunks
