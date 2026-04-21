"""Recruiter Platform 实例化辅助函数。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.platforms import get_recruiter_platform

if TYPE_CHECKING:
	import click
	from boss_agent_cli.auth.manager import AuthManager


def get_recruiter_platform_instance(ctx: "click.Context", auth: "AuthManager"):
	obj = ctx.obj or {}
	name = obj.get("platform") or "zhipin"
	recruiter_name = f"{name}-recruiter"
	plat_cls = get_recruiter_platform(recruiter_name)

	delay = obj.get("delay", (1.5, 3.0))
	cdp_url = obj.get("cdp_url")
	client = BossRecruiterClient(auth, delay=delay, cdp_url=cdp_url)
	return plat_cls(client)


__all__ = ["get_recruiter_platform_instance"]
