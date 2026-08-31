"""Recruiter Platform 实例化辅助函数。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.platforms import get_recruiter_platform
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
	import click
	from boss_agent_cli.auth.manager import AuthManager


def get_recruiter_platform_instance(ctx: "click.Context", auth: "AuthManager") -> RecruiterPlatform:
	obj = ctx.obj or {}
	return build_recruiter_platform_instance(
		obj.get("platform") or "zhipin",
		auth,
		delay=obj.get("delay", (1.5, 3.0)),
		cdp_url=obj.get("cdp_url"),
		browser_mode=obj.get("browser_mode"),
	)


def build_recruiter_platform_instance(
	name: str,
	auth: "AuthManager",
	*,
	delay: tuple[float, float] = (1.5, 3.0),
	cdp_url: str | None = None,
	browser_mode: str | None = None,
) -> RecruiterPlatform:
	"""Build a recruiter platform without requiring a Click context."""
	recruiter_name = f"{name}-recruiter"
	plat_cls = get_recruiter_platform(recruiter_name)
	if browser_mode is None:
		client = BossRecruiterClient(auth, delay=delay, cdp_url=cdp_url)
	else:
		client = BossRecruiterClient(auth, delay=delay, cdp_url=cdp_url, browser_mode=browser_mode)
	return plat_cls(client)


__all__ = ["build_recruiter_platform_instance", "get_recruiter_platform_instance"]
