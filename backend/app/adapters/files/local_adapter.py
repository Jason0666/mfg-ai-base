"""本地文件存储 Adapter。

- save(path, content) → 实际写入 FILE_ROOT/path，返回相对路径
- load(path) → 读取
- list(prefix) → 列出
- 安全：禁止 path 越界 FILE_ROOT（防止 ../ 越权访问）
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import List

from app.adapters.base import FileStore

logger = logging.getLogger(__name__)


class LocalFileStore(FileStore):
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalFileStore ready: root=%s", self._root)

    def _safe_full(self, path: str) -> Path:
        """规范化路径，禁止越界。"""
        full = (self._root / path).resolve()
        if self._root not in full.parents and full != self._root:
            raise ValueError(f"illegal file path: {path}")
        return full

    def save(self, path: str, content: bytes) -> str:
        full = self._safe_full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
        return path

    def save_with_unique_name(self, filename: str, content: bytes) -> str:
        """生成唯一文件名保存，返回相对路径（供 /api/files/upload 使用）。"""
        ext = os.path.splitext(filename)[1]
        unique = f"{uuid.uuid4().hex}{ext}"
        return self.save(unique, content)

    def load(self, path: str) -> bytes:
        full = self._safe_full(path)
        return full.read_bytes()

    def list(self, prefix: str = "") -> List[str]:
        base = self._root / prefix if prefix else self._root
        if not base.exists():
            return []
        return [
            str(p.relative_to(self._root)).replace(os.sep, "/")
            for p in base.rglob("*")
            if p.is_file()
        ]

    def full_path(self, path: str) -> Path:
        """暴露绝对路径（仅后端内部使用）。"""
        return self._safe_full(path)
