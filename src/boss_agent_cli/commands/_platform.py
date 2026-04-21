"""Platform 实例化辅助函数。

命令层统一通过 ``get_platform_instance(ctx, auth)`` 拿到 Platform 实现，
不直接依赖具体的 ``BossClient``，为多平台适配器铺路（Issue #129 Week 1b）。

示例::

    from boss_agent_cli.commands._platform import get_platform_instance

    @click.command()
    @click.pass_context
    def cmd(ctx: click.Context) -> None:
        auth = AuthManager(ctx.obj["data_dir"])
        platform = get_platform_instance(ctx, auth)
        result = platform.search_jobs("Python", city="广州")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.zhilian_client import ZhilianClient
from boss_agent_cli.platforms import Platform, get_platform

if TYPE_CHECKING:
	import click

	from boss_agent_cli.auth.manager import AuthManager


def _build_client(name: str, auth: "AuthManager", delay: tuple[float, float], cdp_url: str | None) -> Any:
	"""按平台名构造对应的内部 client。"""
	if name == "zhilian":
		return ZhilianClient(auth, delay=delay, cdp_url=cdp_url)
	# 默认 zhipin 走 BossClient
	return BossClient(auth, delay=delay, cdp_url=cdp_url)


def get_platform_instance(ctx: "click.Context", auth: "AuthManager") -> Platform:
	"""根据 ctx.obj["platform"] 构造 Platform 实例。

	- 读取 ``ctx.obj`` 中的 ``platform`` / ``delay`` / ``cdp_url`` 配置
	- 未设 platform 时 fallback 到 "zhipin"
	- 未知平台抛 ``ValueError``
	- 按平台名分发到对应 client（zhipin→BossClient / zhilian→ZhilianClient）
	"""
	obj = ctx.obj or {}
	name = obj.get("platform") or "zhipin"
	plat_cls = get_platform(name)

	delay = obj.get("delay", (1.5, 3.0))
	cdp_url = obj.get("cdp_url")
	client = _build_client(name, auth, delay, cdp_url)
	return plat_cls(client)


__all__ = ["get_platform_instance"]
