"""全局配置：唯一通过环境变量驱动的配置入口。

对应施工蓝图 §11。所有模型名/数据库地址/驱动选择一律走这里，禁止在业务代码硬编码。
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 已知弱密钥占位值（历史版本默认值），生产环境一律拒绝
_WEAK_SECRET_KEYS = {"", "please-change-me", "change-me", "secret", "changeme"}


class Settings(BaseSettings):
    """运行时配置。DEMO_MODE 影响面集中在本类与少量注入点（§5.3）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 运行模式 =====
    DEMO_MODE: bool = True
    APP_ENV: str = "dev"  # dev | prod
    # 无默认值：生产模式必须通过环境变量注入强随机密钥（>=32 字符）
    SECRET_KEY: str = ""

    # ===== 服务 =====
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    FRONTEND_PORT: int = 5173
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost"

    # ===== 关系数据库 =====
    DB_DRIVER: str = "sqlite"  # postgres | sqlite | mysql | dm | kingbase
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_NAME: str = "mfgai"
    DB_USER: str = "mfgai"
    DB_PASSWORD: str = "change-me"
    SQLITE_PATH: str = "./data/mfgai.db"

    # ===== 向量库 =====
    VECTOR_DRIVER: str = "pgvector"  # pgvector | chroma | milvus
    VECTOR_DIM: int = 1024

    # ===== 文件存储 =====
    FILE_DRIVER: str = "local"  # local | smb | minio
    FILE_ROOT: str = "./data/files"

    # ===== LLM =====
    LLM_DRIVER: str = "openai_compat"  # openai_compat | ollama | vllm
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRY: int = 2

    # ===== 向量化模型 =====
    EMBED_DRIVER: str = "openai_compat"
    EMBED_BASE_URL: str = ""
    EMBED_API_KEY: str = ""
    EMBED_MODEL: str = "bge-m3"

    # ===== 业务配置 =====
    AUDIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 20
    NL2SQL_MAX_ROWS: int = 1000

    # ===== 访客时效访问（§2.1）=====
    GUEST_DEFAULT_HOURS: int = 1        # 链接默认有效期（可选 1/6/24）
    GUEST_MAX_CALLS: int = 30           # 默认调用次数上限
    GUEST_RATE_PER_MINUTE: int = 5      # 每 token 每分钟 invoke 频率上限
    GUEST_REAL_LLM_MAX: int = 10        # 高级访客真实 LLM 配额默认值

    # ===== 路径（自动派生，不暴露给客户）=====
    BASE_DIR: str = "./"
    MODULES_DIR: str = "./app/modules"

    # 行业包过滤：设为 manufacturing / government 时只加载对应 industry 的模块；
    # 留空则加载全部模块（默认行为，兼容已有部署）
    INDUSTRY_PACKAGE: str = ""

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _split_cors(cls, v: str) -> str:
        return v

    @property
    def cors_origins_list(self) -> List[str]:
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        # 生产环境禁止 * 通配（§2.3）：必须显式枚举前端来源
        if self.is_prod and any(o == "*" for o in origins):
            raise RuntimeError(
                "CORS_ORIGINS 生产环境禁止使用 '*' 通配，"
                "请显式配置前端来源（逗号分隔），如：https://xxx.pages.dev"
            )
        return origins

    @property
    def db_url(self) -> str:
        """构造 SQLAlchemy URL。DEMO 默认 sqlite，无需 PG 依赖。"""
        if self.DB_DRIVER == "sqlite":
            return f"sqlite:///{self.SQLITE_PATH}"
        if self.DB_DRIVER == "postgres":
            return (
                f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        # 其他驱动留给 M3 信创适配
        return f"sqlite:///{self.SQLITE_PATH}"

    @property
    def llm_ready(self) -> bool:
        """是否配置了真实 LLM Key。"""
        return bool(self.LLM_API_KEY and self.LLM_API_KEY.strip())

    @property
    def is_prod(self) -> bool:
        """生产模式判定：显式 APP_ENV=prod 或 DEMO_MODE=false。"""
        return self.APP_ENV.lower() == "prod" or not self.DEMO_MODE

    def validate_security(self) -> None:
        """启动期安全校验（fail-fast）：生产模式密钥缺失/弱密钥直接拒绝启动。"""
        key = (self.SECRET_KEY or "").strip()
        if self.is_prod:
            if key in _WEAK_SECRET_KEYS:
                raise RuntimeError(
                    "SECRET_KEY 未配置或为弱占位值。生产模式必须通过环境变量注入强随机密钥"
                    "（建议 >=32 字符，例如：python -c \"import secrets;print(secrets.token_hex(32))\"）。"
                )
            if len(key) < 16:
                raise RuntimeError("SECRET_KEY 长度不足，生产模式要求至少 16 字符（建议 64 字符）。")

    @property
    def jwt_secret(self) -> str:
        """JWT 签名密钥。

        - 生产模式：必须显式配置，启动时 validate_security() 已校验
        - DEMO 模式：未配置时进程级随机生成（重启后旧 token 全部失效，仅本地演示用）
        """
        key = (self.SECRET_KEY or "").strip()
        if key and key not in _WEAK_SECRET_KEYS:
            return key
        if self.is_prod:
            # 正常不会走到（启动已 fail-fast），双保险
            raise RuntimeError("SECRET_KEY 未配置，无法签发 JWT。")
        global _DEMO_FALLBACK_KEY
        if not _DEMO_FALLBACK_KEY:
            _DEMO_FALLBACK_KEY = secrets.token_hex(32)
            logger.warning(
                "SECRET_KEY 未配置：DEMO 模式使用进程级随机密钥（重启后登录态失效，仅供本地演示）"
            )
        return _DEMO_FALLBACK_KEY


# DEMO 模式进程级回退密钥
_DEMO_FALLBACK_KEY = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 模块级单例：方便 import 使用
settings = get_settings()
