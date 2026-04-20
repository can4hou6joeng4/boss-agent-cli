"""Platform 注册表：根据 name 返回对应 Platform 实现类。

使用示例:

    from boss_agent_cli.platforms import get_platform
    plat_cls = get_platform("zhipin")  # 默认
    platform = plat_cls(boss_client)
    result = platform.search_jobs("Python")
"""

from __future__ import annotations

from boss_agent_cli.platforms.base import Platform
from boss_agent_cli.platforms.zhipin import BossPlatform

_REGISTRY: dict[str, type[Platform]] = {
	"zhipin": BossPlatform,
}


def get_platform(name: str | None = "zhipin") -> type[Platform]:
	"""按名称获取 Platform 实现类。

	- ``name=None`` 或空字符串 → 返回默认 BOSS 直聘
	- 未知名称 → 抛 ValueError
	"""
	key = name or "zhipin"
	if key not in _REGISTRY:
		available = ", ".join(sorted(_REGISTRY.keys()))
		raise ValueError(f"unknown platform: {key!r}, available: [{available}]")
	return _REGISTRY[key]


def list_platforms() -> list[str]:
	"""返回所有已注册平台名称。"""
	return sorted(_REGISTRY.keys())


def register_platform(name: str, cls: type[Platform]) -> None:
	"""动态注册平台实现（主要给测试用）。"""
	_REGISTRY[name] = cls


__all__ = [
	"Platform",
	"BossPlatform",
	"get_platform",
	"list_platforms",
	"register_platform",
]
