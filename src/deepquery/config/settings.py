from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 运行环境枚举：development=开发 / production=生产 / testing=测试
Env = Literal["development", "production", "testing"]


def _env_file() -> str:
    # 根据 DEEPQUERY_ENV 决定加载哪个 .env.* 文件
    import os

    env = os.getenv("DEEPQUERY_ENV", "development")
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

    env: Env = "development"
    # 来自 https://token.cvte.com 的 API Key，透传给底层 CLI 工具
    api_key: str = ""
    # Claude/Anthropic 兼容网关地址；公司场景填 https://token.cvte.com/... 即可走自建网关
    claude_base_url: str = ""
    # 默认使用的 CLI 适配器名称，可被单次请求覆盖
    default_adapter: str = "claude_code"

    host: str = "0.0.0.0"
    port: int = 8000

    # 单次 CLI 调用的硬超时（秒）。0 表示不限制
    cli_timeout_seconds: int = 120

    # 本地 Markdown 知识库根目录（后续 knowledge/ 模块使用）
    knowledge_dir: Path = Field(default=Path("./knowledge_base"))

    # 是否为 CLI 工具自动生成 MCP 配置（默认开启 filesystem MCP 暴露知识库）
    mcp_enabled: bool = True
    # 额外允许 MCP filesystem 访问的目录；若为空则只暴露 knowledge_dir
    mcp_extra_dirs: list[Path] = Field(default_factory=list)
    # 容器/无人值守环境下默认跳过 claude 的工具权限确认；本地调试可关
    mcp_skip_permissions: bool = True

    # 每个适配器的额外配置（比如自定义 base_url），适配器各自取用
    adapter_options: dict[str, dict[str, str]] = Field(default_factory=dict)


@lru_cache
def get_settings() -> Settings:
    # 单例缓存，避免每次请求都重新解析环境变量
    return Settings()
