"""Platform 注册表：根据 name 返回对应 Platform 实现类。

使用示例:

    from boss_agent_cli.platforms import get_platform
    plat_cls = get_platform("zhipin")  # 默认
    platform = plat_cls(boss_client)
    result = platform.search_jobs("Python")

招聘者平台:

    from boss_agent_cli.platforms import get_recruiter_platform
    recruiter_cls = get_recruiter_platform("zhipin-recruiter")
    recruiter = recruiter_cls(boss_client)
    result = recruiter.list_applications()
"""

from __future__ import annotations

from boss_agent_cli.platforms.base import Platform
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform
from boss_agent_cli.platforms.zhilian import ZhilianPlatform
from boss_agent_cli.platforms.zhipin import BossPlatform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

_REGISTRY: dict[str, type[Platform]] = {
	"zhipin": BossPlatform,
	"zhilian": ZhilianPlatform,
}

_RECRUITER_REGISTRY: dict[str, type[RecruiterPlatform]] = {
	"zhipin-recruiter": BossRecruiterPlatform,
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


def get_recruiter_platform(name: str | None = "zhipin-recruiter") -> type[RecruiterPlatform]:
	"""按名称获取 RecruiterPlatform 实现类。

	- ``name=None`` 或空字符串 → 返回默认 BOSS 直聘招聘者
	- 未知名称 → 抛 ValueError
	"""
	key = name or "zhipin-recruiter"
	if key not in _RECRUITER_REGISTRY:
		available = ", ".join(sorted(_RECRUITER_REGISTRY.keys()))
		raise ValueError(f"unknown recruiter platform: {key!r}, available: [{available}]")
	return _RECRUITER_REGISTRY[key]


def list_recruiter_platforms() -> list[str]:
	"""返回所有已注册招聘者平台名称。"""
	return sorted(_RECRUITER_REGISTRY.keys())


__all__ = [
	"Platform",
	"BossPlatform",
	"ZhilianPlatform",
	"get_platform",
	"list_platforms",
	"register_platform",
	"RecruiterPlatform",
	"BossRecruiterPlatform",
	"get_recruiter_platform",
	"list_recruiter_platforms",
]
