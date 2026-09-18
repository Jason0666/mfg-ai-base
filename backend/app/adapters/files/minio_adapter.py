"""MinIO 文件存储 Adapter —— 架构预留接口，当前版本【尚未实现】。

注意：本文件仅占位，不代表系统当前支持 MinIO。工厂层遇到 FILE_DRIVER=minio
会直接抛出明确错误；如需使用须先完成本适配器开发与联调。
"""
from __future__ import annotations


class MinioAdapter:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "MinIO 适配器为架构预留，当前版本尚未实现；如需使用请联系交付团队。"
        )
