"""文档模板引擎（M2 docx + M3 PDF 双渲染）。

用途：
- M03 质量 8D 报告导出 Word / PDF
- M02 标书审核报告导出（复用同一文档模型）

设计（P8 模板可配置）：
- template_path 指向 YAML 模板描述文件，定义 doc_title/subtitle/章节顺序与标题/footer_note；
  客户可替换为自己的现行 8D 模板而无需改代码。
- data 为通用文档模型（与业务无关）：
    {
      "meta": [(标签, 值), ...],                 # 封面信息表
      "sections": {                              # key 与 descriptor.sections[].code 对应
        "D1": {"paragraphs": [...], "bullets": [...],
               "tables": [{"title": ..., "headers": [...], "rows": [[...], ...]}]},
        ...
      },
      "appendix": [ table, ... ]                 # 附录表（同 tables 元素结构）
    }

PDF 用 PyMuPDF 内置中文字体 china-s（宋体形态），无需外部字体文件。
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

logger = logging.getLogger(__name__)


def _unescape_obj(obj: Any) -> Any:
    """递归反转义 LLM 输出中的 HTML 实体（&gt; &lt; &amp; &nbsp; 等）。

    LLM 偶尔把 markdown/比较符号转成实体写入 JSON，导出前统一还原，
    保证 Word/PDF 中出现的是 >、<、& 等正常字符。
    """
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {k: _unescape_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unescape_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_unescape_obj(v) for v in obj)
    return obj


def _apply_cn_fonts(doc: Document) -> None:
    """设置正文/标题的中文字体（正文宋体、标题黑体），西文 Calibri。"""
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    for style_name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        try:
            st = doc.styles[style_name]
        except KeyError:
            continue
        st.font.name = "Calibri"
        st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")


def _add_kv_table(doc: Document, pairs: List[tuple]) -> None:
    """封面元信息表（两列，自动按 4 行 2 对排版）。"""
    pairs = [(str(k), "" if v is None else str(v)) for k, v in pairs if str(k)]
    if not pairs:
        return
    table = doc.add_table(rows=len(pairs), cols=2)
    table.style = "Table Grid"
    table.columns[0].width = Pt(110)
    for i, (k, v) in enumerate(pairs):
        c0 = table.cell(i, 0)
        c0.text = k
        c0.width = Pt(110)
        for run in c0.paragraphs[0].runs:
            run.bold = True
        table.cell(i, 1).text = v


def _add_table(doc: Document, spec: Dict[str, Any]) -> None:
    """渲染一张带标题的网格表。spec: {title, headers:[], rows:[[]]}"""
    title = str(spec.get("title", "") or "")
    if title:
        p = doc.add_paragraph()
        run = p.add_run(title)
        run.bold = True
        run.font.size = Pt(10.5)
    headers = [str(h) for h in (spec.get("headers") or [])]
    rows = spec.get("rows") or []
    ncols = len(headers)
    if ncols == 0:
        # 无表头时按首行列数
        ncols = len(rows[0]) if rows else 0
    if ncols == 0:
        return
    table = doc.add_table(rows=1 + len(rows), cols=ncols)
    table.style = "Table Grid"
    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for i, row in enumerate(rows, start=1):
        for j in range(ncols):
            val = row[j] if j < len(row) else ""
            table.cell(i, j).text = "" if val is None else str(val)


def _add_section(doc: Document, title: str, content: Optional[Dict[str, Any]]) -> None:
    """渲染一个章节：段落 + 要点列表 + 表格。"""
    doc.add_heading(title, level=1)
    if not content:
        doc.add_paragraph("（待补充）")
        return
    for para in content.get("paragraphs") or []:
        if str(para).strip():
            doc.add_paragraph(str(para))
    for bullet in content.get("bullets") or []:
        if str(bullet).strip():
            doc.add_paragraph(f"• {bullet}")
    for table_spec in content.get("tables") or []:
        _add_table(doc, table_spec)
    # 章节完全为空时给占位，避免 Word 中出现空白章节
    if not any([content.get("paragraphs"), content.get("bullets"), content.get("tables")]):
        doc.add_paragraph("（待补充）")


def render_to_docx(template_path: str, data: dict, output_path: str) -> str:
    """按 YAML 模板描述 + 通用文档数据渲染 Word，返回 output_path。

    template_path 为 None 或文件不存在时，退化为无模板（仅渲染 data 中的内容）。
    """
    descriptor: Dict[str, Any] = {}
    if template_path:
        tp = Path(template_path)
        if tp.exists():
            descriptor = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
        else:
            logger.warning("template descriptor not found: %s, render without descriptor", tp)

    # 统一反转义 LLM 文本中的 HTML 实体
    data = _unescape_obj(data)

    doc = Document()
    _apply_cn_fonts(doc)

    # ===== 封面标题 =====
    doc_title = str(descriptor.get("doc_title") or data.get("doc_title") or "分析报告")
    title_p = doc.add_heading(doc_title, level=0)
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = descriptor.get("subtitle") or data.get("subtitle")
    if subtitle:
        sp = doc.add_paragraph(str(subtitle))
        sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in sp.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    # ===== 元信息表 =====
    meta = data.get("meta") or []
    if meta:
        _add_kv_table(doc, [tuple(pair) for pair in meta])
    doc.add_paragraph("")

    # ===== 章节（按 descriptor 顺序，保证 D1-D8 齐全）=====
    sections_data = data.get("sections") or {}
    section_defs = descriptor.get("sections")
    if section_defs:
        for sd in section_defs:
            code = str(sd.get("code", ""))
            heading = str(sd.get("title", code))
            _add_section(doc, heading, sections_data.get(code))
    else:
        # 无模板描述：按 data 中出现顺序渲染
        for code, content in sections_data.items():
            _add_section(doc, str(code), content)

    # ===== 附录表 =====
    for spec in data.get("appendix") or []:
        doc.add_page_break()
        _add_table(doc, spec)

    # ===== 页脚说明 =====
    footer_note = descriptor.get("footer_note") or data.get("footer_note")
    if footer_note:
        doc.add_paragraph("")
        fp = doc.add_paragraph(str(footer_note))
        for run in fp.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x90, 0x90, 0x90)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    logger.info("docx rendered: %s (sections=%d)", out, len(section_defs or sections_data))
    return str(out)


# ---------------------------------------------------------------------------
# PDF 渲染（M3）：PyMuPDF + 内置中文字体 china-s
# ---------------------------------------------------------------------------
PDF_PAGE_W, PDF_PAGE_H = 595, 842  # A4
PDF_ML, PDF_MR, PDF_MT, PDF_MB = 52, 52, 62, 56
PDF_FONT = "china-s"
PDF_USABLE_W = PDF_PAGE_W - PDF_ML - PDF_MR


def _char_w(ch: str, fs: float) -> float:
    """CJK 字宽约等于字号，ASCII 约 0.55 倍字号。"""
    if ord(ch) < 128:
        return fs * 0.55
    return fs * 1.0


def _wrap_w(text: str, fs: float, max_w: float) -> list:
    """按显示宽度折行，返回行列表。"""
    lines: list = []
    cur, w = "", 0.0
    for ch in str(text):
        cw = _char_w(ch, fs)
        if w + cw > max_w and cur:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    lines.append(cur if cur else "")
    return lines


def _line_count(text: str, fs: float, max_w: float) -> int:
    return max(1, len(_wrap_w(text, fs, max_w)))


class _PdfDoc:
    """A4 流式排版器：页管理 + 段落/表格绘制。"""

    def __init__(self) -> None:
        import fitz

        self.fitz = fitz
        self.doc = fitz.open()
        self.page = self._new_page()
        self.y = float(PDF_MT)

    def _new_page(self):  # type: ignore[no-untyped-def]
        page = self.doc.new_page(width=PDF_PAGE_W, height=PDF_PAGE_H)
        # 页脚页码
        page.insert_text(
            (PDF_PAGE_W / 2 - 12, PDF_PAGE_H - 30),
            str(self.doc.page_count),
            fontname=PDF_FONT, fontsize=8.5, color=(0.55, 0.55, 0.55),
        )
        return page

    def need(self, height: float) -> None:
        if self.y + height > PDF_PAGE_H - PDF_MB:
            self.page = self._new_page()
            self.y = float(PDF_MT)

    def text(
        self, x: float, s: str, fs: float = 10.5,
        color=(0.1, 0.1, 0.1), leading: float = 0,
        max_w: float = 0.0,
    ) -> None:
        """按宽度自动折行的正文文本。"""
        wrap_w = max_w or (PDF_USABLE_W - (x - PDF_ML))
        for ln in _wrap_w(s, fs, wrap_w):
            self.need(fs * 1.7)
            self.page.insert_text((x, self.y + fs), ln, fontname=PDF_FONT,
                                  fontsize=fs, color=color)
            self.y += leading or fs * 1.7

    def heading(self, s: str, level: int = 1) -> None:
        fs = {0: 21, 1: 14.5, 2: 12.5}.get(level, 11.5)
        self.need(fs * 2.6 + 8)
        self.y += 8
        color = (0.08, 0.16, 0.30) if level >= 1 else (0.05, 0.10, 0.20)
        if level == 0:
            # 居中大标题
            self.page.insert_text(
                (PDF_PAGE_W / 2 - len(s) * fs / 2, self.y + fs), s,
                fontname=PDF_FONT, fontsize=fs, color=color)
            self.y += fs * 1.9
        else:
            self.page.insert_text((PDF_ML, self.y + fs), s, fontname=PDF_FONT,
                                  fontsize=fs, color=color)
            self.y += fs * 1.9
            # 标题下划线
            self.page.draw_line(
                (PDF_ML, self.y - 4), (PDF_PAGE_W - PDF_MR, self.y - 4),
                color=(0.72, 0.76, 0.82), width=0.7)

    def hline(self, gap: float = 6.0) -> None:
        self.need(gap + 2)
        self.y += gap

    def kv_table(self, pairs: list) -> None:
        pairs = [(str(k), "" if v is None else str(v)) for k, v in pairs if str(k)]
        if not pairs:
            return
        self._table(
            headers=None,
            rows=[[k, v] for k, v in pairs],
            col_widths=[110.0, PDF_USABLE_W - 110.0],
            header_bg=False,
        )

    def table(self, spec: dict) -> None:
        title = str(spec.get("title", "") or "")
        if title:
            self.need(26)
            self.page.insert_text((PDF_ML, self.y + 11), title, fontname=PDF_FONT,
                                  fontsize=10.5, color=(0.1, 0.1, 0.1))
            self.y += 22
        headers = [str(h) for h in (spec.get("headers") or [])]
        rows = [[("" if c is None else str(c)) for c in r] for r in (spec.get("rows") or [])]
        if not headers and rows:
            headers = [f"列{j + 1}" for j in range(len(rows[0]))]
        if not headers:
            return
        self._table(headers=headers, rows=rows, header_bg=True)

    def _table(
        self, headers: list, rows: list, col_widths: list = None,
        header_bg: bool = True,
    ) -> None:
        ncols = len(headers) if headers else len(rows[0])
        # 列宽：按各列最大单字符内容宽分配，限制在 [0.12, 0.42] 占比
        if col_widths is None:
            maxw = [0.0] * ncols
            sample_rows = ([headers] if headers else []) + rows
            for r in sample_rows:
                for j in range(ncols):
                    val = str(r[j]) if j < len(r) else ""
                    fs = 9.5
                    w = sum(_char_w(ch, fs) for ch in val[:24])
                    maxw[j] = max(maxw[j], min(w, PDF_USABLE_W * 0.42))
            floor = PDF_USABLE_W * 0.12
            maxw = [max(w, floor) for w in maxw]
            total = sum(maxw)
            col_widths = [w / total * PDF_USABLE_W for w in maxw]

        line_h = 13.5
        pad = 4.0
        fs = 9.5

        def draw_row(row: list, bold_header: bool) -> float:
            """绘制一行，返回行高。"""
            cells = [str(row[j]) if j < len(row) else "" for j in range(ncols)]
            nlines = [
                _line_count(c, fs, col_widths[j] - pad * 2)
                for j, c in enumerate(cells)
            ]
            row_h = max(nlines) * line_h + pad * 2
            if self.y + row_h > PDF_PAGE_H - PDF_MB:
                self.page = self._new_page()
                self.y = float(PDF_MT)
            x = PDF_ML
            top = self.y
            # 表头底色
            if bold_header and header_bg:
                self.page.draw_rect(
                    self.fitz.Rect(x, top, PDF_PAGE_W - PDF_MR, top + row_h),
                    color=None, fill=(0.90, 0.92, 0.95))
            for j, c in enumerate(cells):
                ty = top + pad
                for ln in _wrap_w(c, fs, col_widths[j] - pad * 2):
                    self.page.insert_text(
                        (x + pad, ty + fs), ln, fontname=PDF_FONT, fontsize=fs,
                        color=(0.08, 0.08, 0.08))
                    ty += line_h
                x += col_widths[j]
            # 边框（横线 + 竖线）
            self.page.draw_line((PDF_ML, top), (PDF_PAGE_W - PDF_MR, top),
                                color=(0.65, 0.68, 0.72), width=0.6)
            self.page.draw_line((PDF_ML, top + row_h), (PDF_PAGE_W - PDF_MR, top + row_h),
                                color=(0.65, 0.68, 0.72), width=0.6)
            x = PDF_ML
            for j in range(ncols + 1):
                self.page.draw_line((x, top), (x, top + row_h),
                                    color=(0.65, 0.68, 0.72), width=0.6)
                if j < ncols:
                    x += col_widths[j]
            self.y = top + row_h
            return row_h

        if headers:
            draw_row(headers, True)
        for r in rows:
            draw_row(r, False)
        self.y += 8


def render_to_pdf(template_path: str, data: dict, output_path: str) -> str:
    """按 YAML 模板描述 + 通用文档数据渲染 PDF，返回 output_path。

    与 render_to_docx 共用同一文档模型；template 缺失时按 data 出现顺序渲染。
    """
    descriptor: dict = {}
    if template_path:
        tp = Path(template_path)
        if tp.exists():
            descriptor = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}

    # 统一反转义 LLM 文本中的 HTML 实体
    data = _unescape_obj(data)

    pdf = _PdfDoc()
    try:
        doc_title = str(descriptor.get("doc_title") or data.get("doc_title") or "分析报告")
        pdf.heading(doc_title, level=0)
        subtitle = descriptor.get("subtitle") or data.get("subtitle")
        if subtitle:
            pdf.hline(4)
            pdf.text(PDF_ML, str(subtitle), fs=9, color=(0.5, 0.5, 0.5))

        pdf.hline(8)
        meta = data.get("meta") or []
        if meta:
            pdf.kv_table([tuple(p) for p in meta])
            pdf.hline(6)

        sections_data = data.get("sections") or {}
        section_defs = descriptor.get("sections")
        if section_defs:
            for sd in section_defs:
                code = str(sd.get("code", ""))
                pdf.heading(str(sd.get("title", code)), level=1)
                _pdf_section(pdf, sections_data.get(code))
        else:
            for code, content in sections_data.items():
                pdf.heading(str(code), level=1)
                _pdf_section(pdf, content)

        for spec in data.get("appendix") or []:
            pdf.page = pdf._new_page()
            pdf.y = float(PDF_MT)
            pdf.table(spec)

        footer_note = descriptor.get("footer_note") or data.get("footer_note")
        if footer_note:
            pdf.hline(10)
            pdf.text(PDF_ML, str(footer_note), fs=8, color=(0.55, 0.55, 0.55))

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.doc.save(str(out))
        logger.info("pdf rendered: %s", out)
        return str(out)
    finally:
        pdf.doc.close()


def _pdf_section(pdf: "_PdfDoc", content: Optional[dict]) -> None:
    """渲染一个章节：段落 + 要点 + 表格（空章节给占位）。"""
    if not content:
        pdf.text(PDF_ML, "（待补充）", fs=10.5, color=(0.45, 0.45, 0.45))
        return
    paras = content.get("paragraphs") or []
    bullets = content.get("bullets") or []
    tables = content.get("tables") or []
    for para in paras:
        if str(para).strip():
            pdf.text(PDF_ML + 2, str(para))
            pdf.hline(3)
    for b in bullets:
        if str(b).strip():
            pdf.text(PDF_ML + 10, f"• {b}")
            pdf.hline(2)
    for t in tables:
        pdf.hline(4)
        pdf.table(t)
    if not any([paras, bullets, tables]):
        pdf.text(PDF_ML, "（待补充）", fs=10.5, color=(0.45, 0.45, 0.45))
