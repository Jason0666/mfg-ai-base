"""GET/POST /api/registry/* 与 /api/admin/registry/reload

对应 §7.1 平台接口。前端模块市场从 /api/registry/modules 拉取。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.core.registry import get_registry
from app.core.schema_render import validate_input_schema, validate_output_schema

router = APIRouter()
logger = logging.getLogger(__name__)


def _manifest_to_dict(m) -> dict:  # type: ignore[no-untyped-def]
    """将 ModuleManifest 序列化为前端可消费的 dict。"""
    data = m.model_dump(mode="json", exclude_none=False)
    # 给前端额外附加 schema 校验结果
    data["_schema_valid"] = {
        "input": validate_input_schema(m.input_schema),
        "output": validate_output_schema(m.output_schema),
    }
    return data


@router.get("/registry/modules")
def list_modules() -> dict:
    reg = get_registry()
    return {
        "code": 0,
        "message": "ok",
        "data": [_manifest_to_dict(m) for m in reg.get_all()],
    }


@router.get("/registry/modules/{code}")
def get_module(code: str) -> dict:
    reg = get_registry()
    m = reg.get_by_code(code)
    if m is None:
        raise HTTPException(status_code=404, detail=f"module not found: {code}")
    return {"code": 0, "message": "ok", "data": _manifest_to_dict(m)}


@router.post("/admin/registry/reload")
def reload_registry() -> dict:
    """重新扫描模块（仅开发环境，§5.1）。"""
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="reload disabled in prod env")
    reg = get_registry()
    # 重新加载：清空并扫描
    reg._registry.clear()  # noqa: SLF001
    reg.scan(Path(settings.MODULES_DIR).resolve())
    return {
        "code": 0,
        "message": "ok",
        "data": {"modules_count": len(reg.get_all())},
    }
