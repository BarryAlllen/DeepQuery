"""适配器调用相关异常。

把底层 OSError / 超时 / 非零退出码等都包装成结构化异常，
方便 API 层做统一处理与稳定的错误响应。
"""

from __future__ import annotations


class AdapterError(Exception):
    """适配器调用失败的基类。`http_status` 默认 502，子类按需覆盖。"""

    http_status: int = 502
    code: str = "adapter_error"

    def __init__(self, message: str, *, adapter: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.adapter = adapter

    def to_dict(self) -> dict:
        return {"code": self.code, "adapter": self.adapter, "message": self.message}


class AdapterNotFoundError(AdapterError):
    """对应的 CLI 二进制在 PATH 中找不到。多发于本机没装。"""

    http_status = 503
    code = "adapter_not_found"


class AdapterTimeoutError(AdapterError):
    """子进程执行超过 `cli_timeout_seconds`。"""

    http_status = 504
    code = "adapter_timeout"


class AdapterRuntimeError(AdapterError):
    """子进程非零退出。stderr 会作为 message 携带。"""

    http_status = 502
    code = "adapter_runtime_error"


class AdapterAuthError(AdapterError):
    """未配置 API Key 或 Key 被远端拒绝。"""

    http_status = 401
    code = "adapter_auth_error"


class UnknownAdapterError(AdapterError):
    """请求里指定的 adapter 名字未注册。"""

    http_status = 400
    code = "unknown_adapter"
