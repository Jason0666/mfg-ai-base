"""POST /api/files/upload, GET /api/files/{file_id}

对应 §7.1 平台接口。文件存储走 adapter（C3）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from app.adapters.factory import get_file_store

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/files/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    content = await file.read()
    store = get_file_store()
    rel_path = store.save_with_unique_name(file.filename, content)
    file_id = uuid.uuid4().hex
    checksum = hashlib.sha256(content).hexdigest()
    # M1：内存元数据；M3 接 app_file 表
    _FILES[file_id] = {
        "id": file_id,
        "filename": file.filename,
        "mime_type": file.content_type or "application/octet-stream",
        "size_bytes": len(content),
        "storage_path": rel_path,
        "checksum": checksum,
        "uploaded_by": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "code": 0,
        "message": "ok",
        "data": {"file_id": file_id, "filename": file.filename, "size": len(content)},
    }


@router.get("/files/{file_id}")
def download_file(file_id: str) -> StreamingResponse:
    meta = _FILES.get(file_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="file not found")
    store = get_file_store()
    try:
        content = store.load(meta["storage_path"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    import io
    from urllib.parse import quote

    # 中文文件名走 RFC 5987（filename* 优先），filename 给 ASCII 兜底
    safe_name = meta["filename"].encode("ascii", "ignore").decode("ascii") or "download"
    disposition = f"attachment; filename={safe_name}; filename*=UTF-8''{quote(meta['filename'])}"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=meta["mime_type"],
        headers={"Content-Disposition": disposition},
    )


# 进程内文件元数据缓冲（M1 用；M3 接 app_file 表）
_FILES: dict[str, dict] = {}


def register_generated_file(filename: str, storage_path: str,
                            mime_type: str = "application/octet-stream") -> str:
    """注册后端生成的文件（如 M03 的 8D Word 报告）到进程内元数据。

    返回 file_id，配合 GET /api/files/{file_id} 下载；export_url 由调用方拼接。
    """
    store = get_file_store()
    try:
        content = store.load(storage_path)
        size = len(content)
    except Exception:  # noqa: BLE001
        size = 0
    file_id = uuid.uuid4().hex
    _FILES[file_id] = {
        "id": file_id,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size,
        "storage_path": storage_path,
        "checksum": "",
        "uploaded_by": "system-generated",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return file_id
