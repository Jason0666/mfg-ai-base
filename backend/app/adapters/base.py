"""数据接入层抽象基类（§5.2）。

业务模块只依赖这些抽象，不感知具体实现。
切换数据源只改 .env，不改业务代码（C2/C3）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, List, Optional


class VectorStore(ABC):
    """向量库抽象。"""

    @abstractmethod
    def upsert(
        self,
        collection: str,
        ids: List[str],
        vectors: List[List[float]],
        metadatas: List[dict],
    ) -> None:
        ...

    @abstractmethod
    def search(
        self,
        collection: str,
        vector: List[float],
        top_k: int = 8,
        filters: Optional[dict] = None,
    ) -> List[dict]:
        ...

    @abstractmethod
    def delete(self, collection: str, ids: List[str]) -> None:
        ...


class RelationalDB(ABC):
    """关系数据库抽象（只读，禁止 DDL/DML）。"""

    @abstractmethod
    def query(self, sql: str, params: Optional[dict] = None) -> List[dict]:
        ...

    @abstractmethod
    def get_schema(self, tables: Optional[List[str]] = None) -> dict:
        ...


class FileStore(ABC):
    """文件存储抽象。"""

    @abstractmethod
    def save(self, path: str, content: bytes) -> str:
        ...

    @abstractmethod
    def load(self, path: str) -> bytes:
        ...

    @abstractmethod
    def list(self, prefix: str) -> List[str]:
        ...


class LLMProvider(ABC):
    """LLM 提供方抽象。"""

    @abstractmethod
    def chat(self, messages: List[dict], **kwargs) -> str:
        ...

    @abstractmethod
    def chat_stream(self, messages: List[dict], **kwargs) -> Iterator[str]:
        ...

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...
