from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepquery.cli_adapters.base import BaseCLIAdapter

# 适配器注册表：name -> 类对象，进程启动时由各适配器模块 import 时自动填充
_REGISTRY: dict[str, type["BaseCLIAdapter"]] = {}


def register(cls: type["BaseCLIAdapter"]) -> type["BaseCLIAdapter"]:
    # 作为类装饰器使用：@register class FooAdapter(BaseCLIAdapter): ...
    if not cls.name:
        raise ValueError(f"{cls.__name__} 必须设置非空的 `name` 字段")
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str, **kwargs) -> "BaseCLIAdapter":
    # 按名字取出适配器并实例化，未注册则抛出明确的错误
    if name not in _REGISTRY:
        raise KeyError(f"未知的 CLI 适配器: {name}. 可选: {list(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def list_adapters() -> list[str]:
    return sorted(_REGISTRY)
