"""Recruiter Platform 实例化辅助函数。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boss_agent_cli.platforms.factory import build_recruiter_platform_instance
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
		browser_source=obj.get("browser_source"),
	)


__all__ = ["get_recruiter_platform_instance"]
