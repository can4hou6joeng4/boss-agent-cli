"""按平台名构造 Platform / RecruiterPlatform 实例（不依赖 Click context）。

命令层经 ``commands._platform`` / ``commands._recruiter_platform`` 从 ctx 取参数后调用这里；
wizard 直接调用。刻意不从 ``platforms/__init__`` re-export，避免导入平台注册表时拉起 HTTP / 浏览器客户端。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.api.browser_source import BrowserSourceUnsupported, resolve_policy
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.zhilian_client import ZhilianClient
from boss_agent_cli.platforms import Platform, get_platform, get_recruiter_platform
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
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
	# 与 build_recruiter_platform_instance 同一写法：唯一有浏览器通道的是 zhipin（BossClient）。
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


def build_recruiter_platform_instance(
	name: str,
	auth: "AuthManager",
	*,
	delay: tuple[float, float] = (1.5, 3.0),
	cdp_url: str | None = None,
	browser_source: str | None = None,
) -> RecruiterPlatform:
	"""Build a recruiter platform without requiring a Click context.

	非 ``auto`` 来源只对有浏览器通道的 zhipin 招聘者 client 有意义；其余平台
	没有招聘者浏览器通道，显式抛 ``BrowserSourceUnsupported``（命令层转
	``NOT_SUPPORTED``），与 ``_build_client`` 同一写法。
	"""
	policy = resolve_policy(browser_source)
	# 守卫必须先于 get_recruiter_platform：非 zhipin 平台未注册 recruiter 适配器，
	# 晚判会被 registry 的 ValueError 抢先，BrowserSourceUnsupported 永远到不了。
	if name != "zhipin" and policy.fail_closed:
		raise BrowserSourceUnsupported(name, policy.name)
	recruiter_name = f"{name}-recruiter"
	plat_cls = get_recruiter_platform(recruiter_name)
	client = BossRecruiterClient(auth, delay=delay, cdp_url=cdp_url, browser_source=policy.name)
	return plat_cls(client)


__all__ = ["build_platform_instance", "build_recruiter_platform_instance"]
