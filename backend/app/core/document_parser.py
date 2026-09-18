"""文档解析（M2 M02 实现）。

对应蓝图能力 file_parse：
- PDF：PyMuPDF 按页提取文本，保留真实页码
- DOCX：python-docx 提取段落；Word 无固定分页概念，按分页符/估算页处理
- 条款切片：识别"第X章/第X条/X.Y/X.Y.Z"等条款编号，把每条挂到出现页

统一输出 PageChunk：
    {page: int(1-based), clause: str, text: str}

设计原则：解析与业务无关，M02 及后续合同/文档审核模块复用。
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 条款号：第X条 / 第X章 / X.Y / X.Y.Z / （一）
_CLAUSE_RE = re.compile(
    r"(第[一二三四五六七八九十百零\d]+[条章节款项]|"
    r"\d+(?:\.\d+){0,3}\s*[、.．]?)"
)
# 明显的标题行（短行 + 章/条）
_HEADLINE_RE = re.compile(r"^\s*(第[一二三四五六七八九十百零\d]+[章节])")


@dataclass
class PageChunk:
    page: int
    clause: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"page": self.page, "clause": self.clause, "text": self.text}


def parse_pdf(content: bytes) -> List[Dict[str, Any]]:
    """按页解析 PDF，返回 [{page, clause, text}]；每页一条，clause 为该页最新条款号。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    pages: List[Dict[str, Any]] = []
    current_clause = ""
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            text = _normalize(text)
            if not text:
                continue
            # 页内追踪条款号（取该页最后出现的条/章号作为归属）
            matches = list(_CLAUSE_RE.finditer(text))
            for m in matches:
                token = m.group(1).strip()
                # 只在像条款号（含"条/章"或带小数点）时更新
                if re.search(r"[条章节]", token) or re.match(r"^\d+\.\d+", token):
                    current_clause = token.rstrip("、.．")
            pages.append({"page": i, "clause": current_clause, "text": text})
    finally:
        doc.close()
    return pages


def parse_docx(content: bytes) -> List[Dict[str, Any]]:
    """解析 Word。

    Word 不存物理页码：优先按显式分页符切页；否则按每约 35 个段落估算一页。
    页码为估算值，报告中标注"约"。
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(content))
    blocks: List[List[str]] = [[]]
    for para in doc.paragraphs:
        t = para.text.strip()
        # 检测分页符
        brks = para._p.findall(".//" + qn("w:br"))
        has_page_break = any(b.get(qn("w:type")) == "page" for b in brks)
        if t:
            blocks[-1].append(t)
        if has_page_break:
            blocks.append([])

    # 无分页符时按段落数估算分页
    flat = [ln for b in blocks for ln in b]
    if len(blocks) == 1 and flat:
        blocks = [flat[i : i + 35] for i in range(0, len(flat), 35)]

    pages: List[Dict[str, Any]] = []
    current_clause = ""
    for i, lines in enumerate(blocks, start=1):
        text = _normalize("\n".join(lines))
        if not text:
            continue
        matches = list(_CLAUSE_RE.finditer(text))
        for m in matches:
            token = m.group(1).strip()
            if re.search(r"[条章节]", token) or re.match(r"^\d+\.\d+", token):
                current_clause = token.rstrip("、.．")
        pages.append({"page": i, "clause": current_clause, "text": text})
    return pages


def _normalize(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_clauses(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把页级 chunks 进一步按"第X条"切成条款级 chunks（同一页多条时拆分）。

    条款级 chunk 继承所在页页码，供精确定位；无法切条时保留页级 chunk。
    """
    out: List[Dict[str, Any]] = []
    for pg in pages:
        text = pg["text"]
        # 找页内所有"第X条"起点
        marks = list(re.finditer(r"第[一二三四五六七八九十百零\d]+条", text))
        if len(marks) <= 1:
            out.append(pg)
            continue
        for j, m in enumerate(marks):
            end = marks[j + 1].start() if j + 1 < len(marks) else len(text)
            seg = text[m.start():end].strip()
            if seg:
                out.append({"page": pg["page"], "clause": m.group(0), "text": seg})
    return out


def parse_document(filename: str, content: bytes) -> List[Dict[str, Any]]:
    """统一入口：按扩展名分发，返回页级 chunks（含 page/clause/text）。"""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        pages = parse_pdf(content)
    elif ext in (".docx", ".doc"):
        pages = parse_docx(content)
    else:
        raise ValueError(f"不支持的文件类型：{ext}（仅支持 PDF / Word）")
    logger.info("document parsed: %s pages=%d", filename, len(pages))
    return pages


def full_text(pages: List[Dict[str, Any]], max_chars: Optional[int] = None) -> str:
    """拼接带页码标记的全文（每段前缀 [第N页]）。"""
    parts = [f"[第{p['page']}页｜{p.get('clause','')}] {p['text']}" for p in pages]
    joined = "\n\n".join(parts)
    if max_chars and len(joined) > max_chars:
        return joined[:max_chars]
    return joined
