from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 运行环境枚举：dev=开发 / prod=生产 / test=测试
Env = Literal["dev", "prod", "test"]


def _env_file() -> str:
    # 根据 DEEPQUERY_ENV 决定加载哪个 .env.* 文件
    import os

    env = os.getenv("DEEPQUERY_ENV", "dev")
    return f".env.{env}"


class Settings(BaseSettings):
    # 所有环境变量统一加 DEEPQUERY_ 前缀，避免污染全局命名空间
    model_config = SettingsConfigDict(
        env_prefix="DEEPQUERY_",
        # 先读 .env（公共配置），再按环境读 .env.dev / .env.prod / .env.test 覆盖
        env_file=(".env", _env_file()),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Env = "dev"
    # 来自 https://token.cvte.com 的 API Key，透传给底层 CLI 工具
    api_key: str = ""
    # 默认使用的 CLI 适配器名称，可被单次请求覆盖
    default_adapter: str = "claude_code"

    host: str = "0.0.0.0"
    port: int = 8000

    # 本地 Markdown 知识库根目录（后续 knowledge/ 模块使用）
    knowledge_dir: Path = Field(default=Path("./knowledge_base"))

    # 每个适配器的额外配置（比如自定义 base_url），适配器各自取用
    adapter_options: dict[str, dict[str, str]] = Field(default_factory=dict)


@lru_cache
def get_settings() -> Settings:
    # 单例缓存，避免每次请求都重新解析环境变量
    return Settings()
