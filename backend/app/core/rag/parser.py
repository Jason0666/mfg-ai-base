"""文档解析（§3.3 PyMuPDF/python-docx/openpyxl，M2 实现，M1 占位）。

支持 PDF / Word / Excel / TXT。保留页码、章节、段落。
"""
from __future__ import annotations


def parse_pdf(path: str) -> list[dict]:
    raise NotImplementedError("parser.parse_pdf will be implemented in M2")


def parse_docx(path: str) -> list[dict]:
    raise NotImplementedError("parser.parse_docx will be implemented in M2")


def parse_xlsx(path: str) -> list[dict]:
    raise NotImplementedError("parser.parse_xlsx will be implemented in M2")


def parse_text(path: str) -> list[dict]:
    raise NotImplementedError("parser.parse_text will be implemented in M2")
