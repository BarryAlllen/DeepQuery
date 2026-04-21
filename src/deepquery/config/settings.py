from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 运行环境枚举：development=开发 / production=生产 / testing=测试
Env = Literal["development", "production", "testing"]


def _env_file() -> str:
    # 根据 DEEPQUERY_ENV 决定加载哪个 .env.* 文件
    import os

    env = os.getenv("DEEPQUERY_ENV", "development")
    return f".env.{env}"


# 知识库可见性范围：
#   public  — 所有用户可读（如 shared/）
#   team    — 团队级，未来 v2 加用户系统时按 team_ids 过滤
#   private — 个人级，未来 v2 加用户系统时按 user_id 过滤
RepoScope = Literal["public", "team", "private"]


class KnowledgeRepo(BaseModel):
    """一份可挂载的知识库定义。

    v1 通常只配一个 public 仓（shared）；
    v2 加多用户后，team/private 仓会按用户身份动态过滤可见性。
    """

    name: str
    # 远程地址，留空表示该 subdir 不来自 Git（纯本地目录）
    url: str = ""
    branch: str = "main"
    # 相对 knowledge_dir 的子目录；多个仓共存时用来避免冲突
    subdir: str
    scope: RepoScope = "public"
    # team / user 过滤所需的 ID 列表，v1 留空
    team_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)


def _default_repos() -> list[KnowledgeRepo]:
    # v1 默认：单仓 + shared 子目录，url 留空（不强制 Git）
    return [KnowledgeRepo(name="shared", subdir="shared", scope="public")]


class Settings(BaseSettings):
    # 所有环境变量统一加 DEEPQUERY_ 前缀，避免污染全局命名空间
    model_config = SettingsConfigDict(
        env_prefix="DEEPQUERY_",
        # 先读 .env（公共配置），再按环境读 .env.dev / .env.prod / .env.test 覆盖
        env_file=(".env", _env_file()),
        env_file_encoding="utf-8",
        extra="ignore",
        # 用于嵌套字段的 env 解析（DEEPQUERY_KNOWLEDGE_REPOS 走 JSON）
        env_nested_delimiter="__",
    )

    env: Env = "development"
    # 上游 LLM API 的访问凭证。公司场景从 https://token.cvte.com 申请；
    # 留空时退回 CLI 自身的本地凭据（如 `claude login`）。
    api_key: str = ""
    # 上游 LLM API 的基础地址。公司场景填公司网关，留空则走 CLI 默认（如 Anthropic 官方）。
    api_base_url: str = ""
    # 默认使用的 CLI 适配器名称，可被单次请求覆盖
    default_adapter: str = "claude_code"

    host: str = "0.0.0.0"
    port: int = 8000

    # 单次 CLI 调用的硬超时（秒）。0 表示不限制
    cli_timeout_seconds: int = 120

    # 本地 Markdown 知识库根目录；下属各子目录由 knowledge_repos 中的 subdir 描述
    knowledge_dir: Path = Field(default=Path("./knowledge_base"))
    # 多仓配置；通过环境变量 DEEPQUERY_KNOWLEDGE_REPOS 传 JSON 数组覆盖
    knowledge_repos: list[KnowledgeRepo] = Field(default_factory=_default_repos)
    # 自动 git pull 间隔（秒），<=0 表示禁用后台自动同步（仍可手动调 /api/knowledge/sync）
    knowledge_auto_pull_seconds: int = 0
    # 给 webhook 用的简单共享密钥；留空则不校验
    knowledge_webhook_secret: str = ""
    # HTTP 写文档接口的最大单文件字节数；防止误传大文件撑爆 git 仓
    knowledge_max_file_bytes: int = 5 * 1024 * 1024

    # 是否为 CLI 工具自动生成 MCP 配置（默认开启 filesystem MCP 暴露知识库）
    mcp_enabled: bool = True
    # 额外允许 MCP filesystem 访问的目录；若为空则只暴露按用户解析出的知识库子目录
    mcp_extra_dirs: list[Path] = Field(default_factory=list)
    # 容器/无人值守环境下默认跳过 claude 的工具权限确认；本地调试可关
    mcp_skip_permissions: bool = True

    # 每个适配器的额外配置（比如自定义 base_url），适配器各自取用
    adapter_options: dict[str, dict[str, str]] = Field(default_factory=dict)

    # 问答历史持久化：默认 SQLite 放知识库同级目录，容器里挂到 /data。
    # 生产想切 Postgres 只改 URL，比如 postgresql+asyncpg://user:pw@host/db
    database_url: str = "sqlite+aiosqlite:///./deepquery.db"
    # 是否打开 SQLAlchemy SQL 回显（排查慢查询时临时开）
    database_echo: bool = False

    @field_validator("knowledge_repos", mode="before")
    @classmethod
    def _empty_repos_to_default(cls, v):
        # 允许 .env 中写空串：等同于使用默认配置（单仓 shared）
        if v in (None, "", []):
            return _default_repos()
        return v


@lru_cache
def get_settings() -> Settings:
    # 单例缓存，避免每次请求都重新解析环境变量
    return Settings()
