"""Platform 实例化辅助函数。

命令层统一通过 ``get_platform_instance(ctx, auth)`` 拿到 Platform 实现，
不直接依赖具体的 ``BossClient``，为多平台适配器铺路（Issue #129 Week 1b）。
实际构造在 ``platforms.factory.build_platform_instance``，这里只负责从 ctx 取参数。

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

from typing import TYPE_CHECKING

from boss_agent_cli.platforms import Platform
from boss_agent_cli.platforms.factory import build_platform_instance

if TYPE_CHECKING:
	import click

	from boss_agent_cli.auth.manager import AuthManager


def get_platform_instance(ctx: "click.Context", auth: "AuthManager") -> Platform:
	"""根据 ctx.obj["platform"] 构造 Platform 实例。

	- 读取 ``ctx.obj`` 中的 ``platform`` / ``delay`` / ``cdp_url`` / ``browser_source`` 配置
	- 未设 platform 时 fallback 到 "zhipin"
	- 未知平台抛 ``ValueError``
	- 按平台名分发到对应 client（zhipin→BossClient / zhilian→ZhilianClient）
	"""
	obj = ctx.obj or {}
	return build_platform_instance(
		obj.get("platform") or "zhipin",
		auth,
		delay=obj.get("delay", (1.5, 3.0)),
		cdp_url=obj.get("cdp_url"),
		browser_source=obj.get("browser_source"),
	)


__all__ = ["get_platform_instance"]
