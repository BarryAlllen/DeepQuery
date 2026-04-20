from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class BaseCLIAdapter(ABC):
    """CLI 工具适配器抽象基类。

    新增一个 CLI 工具只需继承本类并实现 `build_command`，
    基类负责启动子进程、把 prompt 通过 stdin 喂入，并以异步方式流式读取 stdout。
    """

    # 适配器唯一名字，用于注册表查找与 API 选择
    name: str = ""

    def __init__(self, api_key: str = "", options: dict[str, str] | None = None) -> None:
        self.api_key = api_key
        # 适配器级别的额外配置（比如自定义 endpoint）
        self.options = options or {}

    @abstractmethod
    def build_command(self, prompt: str) -> list[str]:
        """返回子进程 argv。prompt 同时通过 stdin 传入，子类自行决定是否使用参数形式。"""

    def build_env(self) -> dict[str, str]:
        """返回要追加到子进程的环境变量（如 API Key），会与当前 os.environ 合并。"""
        return {}

    async def run(self, prompt: str) -> AsyncIterator[str]:
        import os

        # 合并环境变量：系统环境 + 适配器自定义，后者优先
        env = {**os.environ, **self.build_env()}
        proc = await asyncio.create_subprocess_exec(
            *self.build_command(prompt),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert proc.stdin and proc.stdout
        # 把 prompt 写入子进程 stdin，然后关闭写端触发 EOF
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        # 按行流式产出，配合 FastAPI StreamingResponse 可以做到边算边推
        async for line in proc.stdout:
            yield line.decode(errors="replace")

        await proc.wait()
        if proc.returncode != 0:
            # 非零退出码：把 stderr 收集起来抛出，方便调用方定位 CLI 层的报错
            err = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"{self.name} exited {proc.returncode}: {err}")
