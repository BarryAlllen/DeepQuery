from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from deepquery.core.exceptions import (
    AdapterNotFoundError,
    AdapterRuntimeError,
    AdapterTimeoutError,
)

if TYPE_CHECKING:
    from deepquery.config.settings import Settings

logger = logging.getLogger(__name__)


class BaseCLIAdapter(ABC):
    """CLI 工具适配器抽象基类。

    新增一个 CLI 工具只需继承本类并实现 `build_command`，
    基类负责启动子进程、把 prompt 通过 stdin 喂入，并以异步方式流式读取 stdout。
    """

    # 适配器唯一名字，用于注册表查找与 API 选择
    name: str = ""

    def __init__(
        self,
        api_key: str = "",
        options: dict[str, str] | None = None,
        settings: "Settings | None" = None,
    ) -> None:
        self.api_key = api_key
        # 适配器级别的额外配置（比如自定义 endpoint）
        self.options = options or {}
        # 全局 settings，给需要 MCP 配置等高级能力的适配器使用；多数子类可忽略
        self._settings: Any = settings

    @abstractmethod
    def build_command(self, prompt: str) -> list[str]:
        """返回子进程 argv。prompt 同时通过 stdin 传入，子类自行决定是否使用参数形式。"""

    def build_env(self) -> dict[str, str]:
        """返回要追加到子进程的环境变量（如 API Key），会与当前 os.environ 合并。"""
        return {}

    def classify_failure(self, returncode: int, stderr: str) -> AdapterRuntimeError:
        """子进程非零退出时的异常构造钩子。

        子类可重写以把通用 `AdapterRuntimeError` 精化为
        `AdapterAuthError` 等更具体的类型，便于 API 层返回更精准的状态码。
        默认实现：原样包装为 `AdapterRuntimeError`。
        """
        return AdapterRuntimeError(
            f"{self.name} 退出码 {returncode}: {stderr.strip() or '(无 stderr 输出)'}",
            adapter=self.name,
        )

    @property
    def timeout_seconds(self) -> float | None:
        # 0 / None 表示不限制；否则用 settings 配置的硬超时
        if not self._settings:
            return None
        v = getattr(self._settings, "cli_timeout_seconds", 0)
        return float(v) if v and v > 0 else None

    async def run(self, prompt: str) -> AsyncIterator[str]:
        env = {**os.environ, **self.build_env()}
        cmd = self.build_command(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            # CLI 二进制不在 PATH——给出明确指引，而不是裸 OSError
            raise AdapterNotFoundError(
                f"未找到可执行文件 `{cmd[0]}`：{e}. 请确认对应 CLI 已安装并在 PATH 中。",
                adapter=self.name,
            ) from e

        assert proc.stdin and proc.stdout and proc.stderr
        try:
            # 把 prompt 写入子进程 stdin，然后关闭写端触发 EOF
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            timeout = self.timeout_seconds
            try:
                # 流式按行读出；整体加一个硬超时兜底，避免 CLI 卡死把 worker 也挂住
                async for line in _iter_with_timeout(proc.stdout, timeout):
                    yield line.decode(errors="replace")
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError as e:
                proc.kill()
                await proc.wait()
                raise AdapterTimeoutError(
                    f"{self.name} 执行超过 {timeout:.0f}s，已被强制终止。",
                    adapter=self.name,
                ) from e

            if proc.returncode != 0:
                err = (await proc.stderr.read()).decode(errors="replace")
                logger.warning("%s exited %s: %s", self.name, proc.returncode, err.strip())
                # 走子类钩子，便于 ClaudeCodeAdapter 等识别 401/auth 等具体场景
                raise self.classify_failure(proc.returncode or -1, err)
        finally:
            # 防御：迭代过程中调用方提前断开时回收子进程
            if proc.returncode is None:
                proc.kill()


async def _iter_with_timeout(
    stream: asyncio.StreamReader, timeout: float | None
) -> AsyncIterator[bytes]:
    """整体超时下按行读取流。`timeout=None` 表示不限制。"""
    if timeout is None:
        async for line in stream:
            yield line
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        line = await asyncio.wait_for(stream.readline(), timeout=remaining)
        if not line:
            return
        yield line
