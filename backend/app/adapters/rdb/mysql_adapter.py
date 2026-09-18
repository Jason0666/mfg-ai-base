"""MySQL Adapter（M3 接入，M1 占位）。"""
from __future__ import annotations


class MysqlAdapter:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        raise NotImplementedError("MysqlAdapter will be implemented in M3")
