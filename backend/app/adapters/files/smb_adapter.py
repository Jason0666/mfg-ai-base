"""SMB/CIFS 文件存储 Adapter —— 架构预留接口，当前版本【尚未实现】。

注意：本文件仅占位，不代表系统当前支持 SMB 共享盘。工厂层遇到 FILE_DRIVER=smb
会直接抛出明确错误；如需使用须先完成本适配器开发与联调。
"""
from __future__ import annotations


class SmbAdapter:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "SMB 适配器为架构预留，当前版本尚未实现；如需使用请联系交付团队。"
        )
