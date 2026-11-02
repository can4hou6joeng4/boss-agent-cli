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

from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
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
	- 按平台名分发到对应 client（zhipin→BossClient）
	"""
	obj = ctx.obj or {}
	return build_platform_instance(
		obj.get("platform") or "zhipin",
		auth,
		delay=obj.get("delay", DEFAULT_REQUEST_DELAY),
		cdp_url=obj.get("cdp_url"),
		browser_source=obj.get("browser_source"),
	)


def account_scope(auth: "AuthManager") -> str:
	"""返回当前登录态的账号指纹，用于本地操作记录的去重作用域。

	未登录或缺少主 Cookie 时返回空串；此时本地记录退化为只按 ``security_id``
	匹配，与引入账号作用域之前的行为一致。非字符串的返回值（测试替身）同样
	按空串处理，避免把 Mock 对象写进 SQLite。
	"""
	value = auth.account_fingerprint()
	return value if isinstance(value, str) else ""


__all__ = ["account_scope", "get_platform_instance"]
