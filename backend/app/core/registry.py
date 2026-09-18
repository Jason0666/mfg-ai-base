"""模块注册中心（§5.1）。

职责：
1. 启动时扫描 app/modules/ 下所有含 manifest.yaml 的目录
2. 用 Pydantic 校验 manifest（§8.1 schema）
3. 校验失败 → 打日志跳过，不阻断启动
4. 校验通过 → 导入 api.router，挂载到 /api/modules/{code}
5. 内存注册表供 /api/registry/modules 查询
6. 按 manifest.order 排序

模块隔离：单个模块导入失败不影响其他模块与后端启动。
热重载：POST /api/admin/registry/reload（仅开发环境）。

关于 §5.1「跳过 _template」的约定：M1 阶段保留 _template 作为可见占位
（M1 DoD 要求首页能看到 _template 模块卡片）；M2 接入首批业务模块后，
会切回 §5.1 原始语义（仅扫描 m* 目录）。详见 TODO 注释。
"""
from __future__ import annotations

import importlib
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# ===== §8.1 manifest 的 Pydantic 校验模型 =====


class PainValue(BaseModel):
    """痛点/价值条目。"""
    pass  # 实际为字符串列表，模型不需要


class DeploymentRequirements(BaseModel):
    data_needed: List[str] = Field(default_factory=list)
    integration: str = ""
    timeline: str = ""
    acceptance_criteria: List[str] = Field(default_factory=list)
    remarks: str = ""


class DemoConfig(BaseModel):
    suggested_questions: List[str] = Field(default_factory=list)
    script_file: str = ""


class RetrievalConfig(BaseModel):
    collections: List[str] = Field(default_factory=list)
    top_k: int = 8
    rerank_top_n: int = 5
    filters: Dict[str, Any] = Field(default_factory=dict)


class ExtraEndpoint(BaseModel):
    path: str
    method: str = "GET"
    handler: str = ""


class ModuleManifest(BaseModel):
    """§8.1 manifest.yaml 完整 Schema 的 Pydantic 校验模型。"""

    # 必填
    code: str
    name: str
    version: str
    category: str  # document | knowledge | data
    order: int = 100
    icon: str = "cube"
    summary: str = ""

    # 行业包标签：留空表示通用（所有行业包都加载）
    industry: str = ""

    # 示例模块标记：true 表示硬编码模拟数据（非真实链路），前端展示角标/横幅且不进入演示动线
    sample: bool = False
    sample_notice: str = ""

    # 痛点与价值
    pain_points: List[str] = Field(default_factory=list)
    value_props: List[str] = Field(default_factory=list)

    # 输入/输出 schema（透传 JSON Schema dict，不二次建模）
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)

    # 能力声明
    capabilities: List[str] = Field(default_factory=list)

    # 检索配置
    retrieval: Optional[RetrievalConfig] = None

    # 演示剧本
    demo: Optional[DemoConfig] = None

    # 落地条件
    deployment_requirements: Optional[DeploymentRequirements] = None

    # 专属接口
    extra_endpoints: List[ExtraEndpoint] = Field(default_factory=list)

    # 运行时附加（不在 yaml 里）
    module_dir: str = ""  # 绝对路径，运行时填入


# ===== 注册中心 =====


class ModuleRegistry:
    """模块注册中心单例。"""

    def __init__(self) -> None:
        self._registry: Dict[str, ModuleManifest] = {}

    def scan(self, modules_dir: Path) -> None:
        """扫描 modules 目录，加载所有合法模块。

        行业包过滤：settings.INDUSTRY_PACKAGE 非空时，只加载 industry 匹配
        或 industry 为空（通用）的模块。
        """
        from app.config import settings

        self._registry.clear()
        if not modules_dir.exists():
            logger.warning("modules dir not found: %s", modules_dir)
            return

        pkg = settings.INDUSTRY_PACKAGE.strip().lower() if settings.INDUSTRY_PACKAGE else ""

        for child in sorted(modules_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name == "_template":
                continue
            manifest_path = child / "manifest.yaml"
            if not manifest_path.exists():
                continue
            self._load_one(child, manifest_path, pkg)

        # 按 order 排序
        self._registry = dict(
            sorted(self._registry.items(), key=lambda kv: kv[1].order)
        )

    def _load_one(self, module_dir: Path, manifest_path: Path, industry_filter: str = "") -> None:
        """加载单个模块。任何异常都只打日志，不抛出。

        industry_filter 非空时，跳过 industry 不匹配的模块
        （industry 为空表示通用，始终加载）。
        """
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = ModuleManifest(**raw)
            manifest.module_dir = str(module_dir)
            # 校验目录名与 code 一致
            if manifest.code != module_dir.name:
                logger.warning(
                    "manifest.code(%s) != dir name(%s), skipped",
                    manifest.code, module_dir.name,
                )
                return
            # 行业包过滤
            mod_industry = (manifest.industry or "").strip().lower()
            if industry_filter and mod_industry and mod_industry != industry_filter:
                logger.info("skipped module %s (industry=%s, filter=%s)",
                            manifest.code, mod_industry, industry_filter)
                return
            self._registry[manifest.code] = manifest
            logger.info("loaded module: %s (order=%d, industry=%s)",
                        manifest.code, manifest.order, mod_industry or "generic")
        except ValidationError as e:
            logger.error("manifest invalid: %s\n%s", manifest_path, e)
        except Exception as e:  # noqa: BLE001
            logger.exception("failed to load module %s: %s", module_dir, e)

    def get_all(self) -> List[ModuleManifest]:
        return list(self._registry.values())

    def get_by_code(self, code: str) -> Optional[ModuleManifest]:
        return self._registry.get(code)


# 全局单例
_registry = ModuleRegistry()


def get_registry() -> ModuleRegistry:
    return _registry


def new_trace_id() -> str:
    return uuid.uuid4().hex


# ===== 路由挂载辅助 =====


def mount_module_routers(app) -> None:  # type: ignore[no-untyped-def]
    """把每个已注册模块的 api.router 挂到 /api/modules/{code}。

    模块隔离：单个模块挂载失败不影响其他模块。
    """
    for manifest in _registry.get_all():
        try:
            module_api = importlib.import_module(f"app.modules.{manifest.code}.api")
            router = getattr(module_api, "router", None)
            if router is None:
                logger.warning("module %s has no router attr, skipped", manifest.code)
                continue
            prefix = f"/api/modules/{manifest.code}"
            app.include_router(router, prefix=prefix)
            logger.info("mounted router: %s", prefix)
        except Exception as e:  # noqa: BLE001
            logger.exception("failed to mount router for %s: %s", manifest.code, e)
