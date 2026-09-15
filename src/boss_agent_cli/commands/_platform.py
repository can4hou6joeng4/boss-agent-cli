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

from boss_agent_cli.api.browser_source import BrowserSourceUnsupported, resolve_policy
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.zhilian_client import ZhilianClient
from boss_agent_cli.platforms import Platform, get_platform

if TYPE_CHECKING:
	import click

	from boss_agent_cli.auth.manager import AuthManager


def _build_client(
	name: str,
	auth: "AuthManager",
	delay: tuple[float, float],
	cdp_url: str | None,
	browser_source: str | None,
) -> Any:
	"""按平台名构造对应的内部 client。

	非 ``auto`` 的浏览器来源只对有浏览器通道的 client（zhipin/BossClient）有意义；
	zhilian 没有浏览器通道，显式抛 ``BrowserSourceUnsupported``，
	由命令层转成 ``NOT_SUPPORTED`` 信封——而不是让 ``ZhilianClient`` 因意外 kwarg
	抛 ``TypeError`` 被兜底成 ``NETWORK_ERROR``。
	"""
	policy = resolve_policy(browser_source)
	# 与 _recruiter_platform 同一写法：唯一有浏览器通道的是 zhipin（BossClient）。
	# 任何其他平台配非 auto 来源都在此抛 BrowserSourceUnsupported（→ NOT_SUPPORTED），
	# 而不是落到占位适配器返回不带 recovery_action 的 NOT_SUPPORTED，或因意外 kwarg
	# 抛 TypeError 被兜底成 NETWORK_ERROR。守卫写成通用式（name != "zhipin"）而非名称
	# 白名单，未来新增无浏览器通道的平台不会静默落到 BossClient。
	if name != "zhipin" and policy.fail_closed:
		raise BrowserSourceUnsupported(name, policy.name)
	if name == "zhilian":
		return ZhilianClient(auth, delay=delay, cdp_url=cdp_url)
	# 默认 zhipin 走 BossClient
	return BossClient(auth, delay=delay, cdp_url=cdp_url, browser_source=policy.name)


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


def build_platform_instance(
	name: str,
	auth: "AuthManager",
	*,
	delay: tuple[float, float] = (1.5, 3.0),
	cdp_url: str | None = None,
	browser_source: str | None = None,
) -> Platform:
	"""Build a candidate platform without requiring a Click context."""
	plat_cls = get_platform(name)
	client = _build_client(name, auth, delay, cdp_url, browser_source)
	return plat_cls(client)


__all__ = ["build_platform_instance", "get_platform_instance"]
