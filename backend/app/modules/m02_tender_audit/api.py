"""M02 招标文件智能审核模块 API（§8.2 标准骨架 + /sample 内置样本）。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.adapters.factory import get_file_store
from app.api.files import register_generated_file
from seed.build_tender_pdf import PDF_PATH, GT_PATH, ensure_sample

from .logic import handle

router = APIRouter()
logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"


@router.post("/invoke")
async def invoke(request: Request):
    payload = await request.json()
    return await handle(payload, request)


@router.get("/sample")
def sample_file() -> dict:
    """注册内置模拟招标文件（66 页、含 5 个废标埋点），返回 file_id 供一键演示。"""
    ensure_sample()
    store = get_file_store()
    content = PDF_PATH.read_bytes()
    storage_path = f"samples/{PDF_PATH.name}"
    store.save(storage_path, content)
    file_id = register_generated_file(PDF_PATH.name, storage_path, PDF_MIME)
    page_count = 0
    try:
        page_count = int(json.loads(GT_PATH.read_text(encoding="utf-8")).get("page_count", 0))
    except Exception:  # noqa: BLE001
        pass
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "file_id": file_id,
            "filename": PDF_PATH.name,
            "size": len(content),
            "page_count": page_count,
            "note": "内置模拟招标文件：智能制造装备采购项目，故意埋入 5 个废标风险点",
        },
    }
