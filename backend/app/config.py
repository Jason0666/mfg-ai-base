"""全局配置：唯一通过环境变量驱动的配置入口。

对应施工蓝图 §11。所有模型名/数据库地址/驱动选择一律走这里，禁止在业务代码硬编码。
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    SECRET_KEY: str = "please-change-me"

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
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 模块级单例：方便 import 使用
settings = get_settings()
