"""BaseCLIAdapter 的错误路径测试：用一个假的子类，避开真实 CLI。"""

import asyncio

import pytest

from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.config.settings import Settings
from deepquery.core.exceptions import (
    AdapterNotFoundError,
    AdapterRuntimeError,
    AdapterTimeoutError,
)


class _Adapter(BaseCLIAdapter):
    """只用于测试的可注入命令的适配器。"""

    name = "fake"

    def __init__(self, cmd: list[str], settings: Settings | None = None) -> None:
        super().__init__(settings=settings)
        self._cmd = cmd

    def build_command(self, prompt: str) -> list[str]:
        return self._cmd


def _settings(timeout: int = 0) -> Settings:
    return Settings(env="testing", cli_timeout_seconds=timeout)


async def _drain(adapter: BaseCLIAdapter) -> str:
    out: list[str] = []
    async for chunk in adapter.run("hello"):
        out.append(chunk)
    return "".join(out)


def test_unknown_executable_raises_not_found():
    adapter = _Adapter(["this-binary-does-not-exist-xyz"])
    with pytest.raises(AdapterNotFoundError) as ei:
        asyncio.run(_drain(adapter))
    assert ei.value.adapter == "fake"
    assert ei.value.http_status == 503


def test_nonzero_exit_raises_runtime_error_with_stderr():
    # python -c 退出码 7 + 写 stderr，跨平台稳定
    adapter = _Adapter(
        ["python", "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"]
    )
    with pytest.raises(AdapterRuntimeError) as ei:
        asyncio.run(_drain(adapter))
    assert "7" in ei.value.message
    assert "boom" in ei.value.message
    assert ei.value.http_status == 502


def test_timeout_kills_process():
    # sleep 5s，超时 1s，应被强杀并抛 timeout
    adapter = _Adapter(["python", "-c", "import time; time.sleep(5)"], settings=_settings(1))
    with pytest.raises(AdapterTimeoutError) as ei:
        asyncio.run(_drain(adapter))
    assert ei.value.http_status == 504


def test_success_streams_output():
    adapter = _Adapter(["python", "-c", "print('hello world')"])
    out = asyncio.run(_drain(adapter))
    assert "hello world" in out
