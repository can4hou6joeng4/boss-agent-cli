from __future__ import annotations

from typing import Any

import click

from boss_agent_cli.display import handle_output
from boss_agent_cli.platforms import get_platform, list_platforms, list_recruiter_platforms

_READONLY_CAPABILITIES = ["search", "detail", "recommend", "me", "status"]
_WRITE_CAPABILITIES = ["greet", "apply"]
_LOCAL_CAPABILITIES = ["shortlist", "stats", "config", "schema"]

_PLATFORM_CAPABILITY_STATUS: dict[str, dict[str, str]] = {
	"zhipin": {
		"search": "available",
		"detail": "available",
		"recommend": "available",
		"me": "available",
		"status": "available",
		"greet": "low_risk_blocked",
		"apply": "low_risk_blocked",
	},
	"zhilian": {
		"search": "available",
		"detail": "available",
		"recommend": "available",
		"me": "available",
		"status": "available",
		"greet": "low_risk_blocked",
		"apply": "low_risk_blocked",
	},
	"qiancheng": {
		"search": "not_supported",
		"detail": "not_supported",
		"recommend": "not_supported",
		"me": "not_supported",
		"status": "placeholder_only",
		"greet": "not_supported",
		"apply": "not_supported",
	},
}

_PLATFORM_NOTES = {
	"zhipin": "默认平台；候选者侧与招聘者侧注册表均已接入。",
	"zhilian": "候选者侧只读链路已接入；招聘者侧暂不可用。",
	"qiancheng": "51job/前程无忧当前仅注册平台身份；真实能力返回 NOT_SUPPORTED。",
}

_ALIAS_NAMES = {
	"51job",
}


def platform_capability_data() -> dict[str, Any]:
	"""Return local-only platform capability metadata without creating clients."""
	candidate_platforms = [name for name in list_platforms() if name not in _ALIAS_NAMES]
	recruiter_platforms = list_recruiter_platforms()
	platforms = []
	for name in candidate_platforms:
		platform_cls = get_platform(name)
		statuses = _PLATFORM_CAPABILITY_STATUS[name]
		platforms.append({
			"name": name,
			"display_name": platform_cls.display_name,
			"base_url": platform_cls.base_url,
			"candidate": True,
			"recruiter": f"{name}-recruiter" in recruiter_platforms,
			"status": "placeholder" if name == "qiancheng" else "available",
			"capabilities": {
				"readonly": {capability: statuses[capability] for capability in _READONLY_CAPABILITIES},
				"write": {capability: statuses[capability] for capability in _WRITE_CAPABILITIES},
				"local": {capability: "available" for capability in _LOCAL_CAPABILITIES},
			},
			"notes": _PLATFORM_NOTES[name],
		})
	return {
		"count": len(platforms),
		"default": "zhipin",
		"aliases": {"51job": "qiancheng"},
		"platforms": platforms,
	}


def _render_platforms(data: dict[str, Any]) -> None:
	lines = ["name\tdisplay_name\tstatus\tcandidate\trecruiter"]
	for item in data["platforms"]:
		candidate = "yes" if item["candidate"] else "no"
		recruiter = "yes" if item["recruiter"] else "no"
		lines.append(f"{item['name']}\t{item['display_name']}\t{item['status']}\t{candidate}\t{recruiter}")
	click.echo("\n".join(lines))


@click.command("platforms")
@click.pass_context
def platforms_cmd(ctx: click.Context) -> None:
	"""列出本地已注册平台与能力状态。"""
	handle_output(
		ctx,
		"platforms",
		platform_capability_data(),
		render=_render_platforms,
		hints={
			"next_actions": [
				"boss --platform <name> status — 检查指定平台本地登录态",
				"boss schema — 查看命令级可用性矩阵",
			],
		},
	)
