from __future__ import annotations

from typing import Any, cast

import click

from boss_agent_cli.display import handle_output
from boss_agent_cli.platforms import get_platform, list_platforms, list_recruiter_platforms

_READONLY_CAPABILITIES = ["search", "detail", "show", "history", "interviews", "recommend", "me", "status"]
_WRITE_CAPABILITIES = ["greet", "apply"]
_LOCAL_CAPABILITIES = ["shortlist", "stats", "config", "schema"]

_PLATFORM_CAPABILITY_STATUS: dict[str, dict[str, str]] = {
	"zhipin": {
		"search": "available",
		"detail": "available",
		"show": "available",
		"history": "available",
		"interviews": "available",
		"recommend": "available",
		"me": "available",
		"status": "available",
		"greet": "available",
		"apply": "available",
	},
	"zhilian": {
		"search": "available",
		"detail": "available",
		"show": "available",
		"history": "available",
		"interviews": "available",
		"recommend": "available",
		"me": "available",
		"status": "available",
		"greet": "available",
		"apply": "available",
	},
}

_CAPABILITY_STATUS_LEGEND: dict[str, dict[str, str]] = {
	"available": {
		"label": "可用",
		"description": "本地 CLI 已接入该能力；是否需要登录仍以具体命令契约为准。",
	},
	"not_supported": {
		"label": "不支持",
		"description": "当前平台适配器没有实现该真实工作流；CLI 会稳定返回 NOT_SUPPORTED。",
	},
}


_PLATFORM_NOTES = {
	"zhipin": "默认平台；候选者侧与招聘者侧注册表均已接入。",
	"zhilian": "候选者侧已接入搜索、详情、投递和沟通；招聘者侧暂不可用。",
}


def _resolve_platform_filter(platform_name: str | None) -> str | None:
	if platform_name is None:
		return None
	candidate_platforms = list_platforms()
	if platform_name not in candidate_platforms:
		supported = ", ".join(candidate_platforms)
		raise click.BadParameter(
			f"unknown platform {platform_name!r}, supported: {supported}",
			param_hint="--platform",
		)
	return platform_name


def _capability_status_for_platform(platform_name: str, capability: str) -> str | None:
	if capability in _LOCAL_CAPABILITIES:
		return "available"
	return _PLATFORM_CAPABILITY_STATUS[platform_name].get(capability)


def _resolve_capability_filter(capability: str | None) -> str | None:
	if capability is None:
		return None
	known_capabilities = [*_READONLY_CAPABILITIES, *_WRITE_CAPABILITIES, *_LOCAL_CAPABILITIES]
	if capability not in known_capabilities:
		supported = ", ".join(known_capabilities)
		raise click.BadParameter(
			f"unknown capability {capability!r}, supported: {supported}",
			param_hint="--capability",
		)
	return capability


def platform_capability_data(platform_name: str | None = None, capability: str | None = None) -> dict[str, Any]:
	"""Return local-only platform capability metadata without creating clients."""
	resolved_platform = _resolve_platform_filter(platform_name)
	resolved_capability = _resolve_capability_filter(capability)
	candidate_platforms = list_platforms()
	if resolved_platform is not None:
		candidate_platforms = [resolved_platform]
	recruiter_platforms = list_recruiter_platforms()
	platforms: list[dict[str, Any]] = []
	for name in candidate_platforms:
		platform_cls = get_platform(name)
		statuses = _PLATFORM_CAPABILITY_STATUS[name]
		item = {
			"name": name,
			"display_name": platform_cls.display_name,
			"base_url": platform_cls.base_url,
			"candidate": True,
			"recruiter": f"{name}-recruiter" in recruiter_platforms,
			"status": "available",
			"capabilities": {
				"readonly": {capability: statuses[capability] for capability in _READONLY_CAPABILITIES},
				"write": {capability: statuses[capability] for capability in _WRITE_CAPABILITIES},
				"local": {capability: "available" for capability in _LOCAL_CAPABILITIES},
			},
			"notes": _PLATFORM_NOTES[name],
		}
		if resolved_capability is not None:
			raw_status = _capability_status_for_platform(name, resolved_capability)
			if raw_status is None:
				continue
			item["capability_match"] = {
				"capability": resolved_capability,
				"status": raw_status,
				"raw_status": raw_status,
			}
		platforms.append(item)
	capability_filter = None
	if resolved_capability is not None:
		status_groups: dict[str, list[str]] = {
			"available": [],
			"blocked_by_policy": [],
			"not_supported": [],
		}
		for item in platforms:
			match = cast(dict[str, str], item["capability_match"])
			status_groups[match["status"]].append(cast(str, item["name"]))
		capability_filter = {
			"capability": resolved_capability,
			"status_groups": status_groups,
		}
	return {
		"count": len(platforms),
		"capability_filter": capability_filter,
		"default": "zhipin",
		"aliases": {},
		"capability_status_legend": _CAPABILITY_STATUS_LEGEND,
		"platforms": platforms,
	}


def _render_platforms(data: dict[str, Any]) -> None:
	if data.get("capability_filter") is not None:
		lines = ["name\tdisplay_name\tstatus\tcandidate\trecruiter\tcapability\tcapability_status"]
	else:
		lines = ["name\tdisplay_name\tstatus\tcandidate\trecruiter"]
	for item in data["platforms"]:
		candidate = "yes" if item["candidate"] else "no"
		recruiter = "yes" if item["recruiter"] else "no"
		row = f"{item['name']}\t{item['display_name']}\t{item['status']}\t{candidate}\t{recruiter}"
		if data.get("capability_filter") is not None:
			match = item["capability_match"]
			row = f"{row}\t{match['capability']}\t{match['status']}"
		lines.append(row)
	lines.append("")
	lines.append("capability_status_legend")
	for status, meta in data["capability_status_legend"].items():
		lines.append(f"{status}\t{meta['label']}\t{meta['description']}")
	click.echo("\n".join(lines))


@click.command("platforms")
@click.option("--platform", "platform_name", default=None, help="仅查看指定已注册平台")
@click.option("--capability", "capability", default=None, help="按能力反查平台状态（如 search / apply / status / schema）")
@click.pass_context
def platforms_cmd(ctx: click.Context, platform_name: str | None, capability: str | None) -> None:
	"""列出本地已注册平台与能力状态。"""
	handle_output(
		ctx,
		"platforms",
		platform_capability_data(platform_name, capability),
		render=_render_platforms,
		hints={
			"next_actions": [
				"boss --platform <name> status — 检查指定平台本地登录态",
				"boss schema — 查看命令级可用性矩阵",
			],
		},
	)
