"""GET /api/health

返回服务/DB/向量库/LLM 连通状态（§7.1）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import settings
from app.core.registry import get_registry

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict:
    # DB 连通
    db_status = {"ok": False, "driver": settings.DB_DRIVER, "error": ""}
    try:
        from app.adapters.factory import get_relational_db
        db = get_relational_db()
        schema = db.get_schema([])
        db_status["ok"] = True
        db_status["tables"] = list(schema.keys())[:20]
    except Exception as e:  # noqa: BLE001
        db_status["error"] = str(e)[:200]

    # 向量库连通
    vec_status = {"ok": False, "driver": settings.VECTOR_DRIVER, "error": ""}
    try:
        from app.adapters.factory import get_vector_store
        get_vector_store()
        vec_status["ok"] = True
    except Exception as e:  # noqa: BLE001
        vec_status["error"] = str(e)[:200]

    # LLM 连通
    llm_status = {
        "ok": False,
        "driver": settings.LLM_DRIVER,
        "model": settings.LLM_MODEL,
        "ready": settings.llm_ready,
        "error": "",
    }
    try:
        from app.adapters.factory import get_llm_provider
        get_llm_provider()
        llm_status["ok"] = True
    except Exception as e:  # noqa: BLE001
        llm_status["error"] = str(e)[:200]

    # 模块数
    modules = get_registry().get_all()

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "app_env": settings.APP_ENV,
            "demo_mode": settings.DEMO_MODE,
            "modules_count": len(modules),
            "modules": [m.code for m in modules],
            "db": db_status,
            "vector": vec_status,
            "llm": llm_status,
        },
    }
