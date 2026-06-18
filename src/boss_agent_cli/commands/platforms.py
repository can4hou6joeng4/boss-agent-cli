from __future__ import annotations

from typing import Any, cast

import click

from boss_agent_cli.display import handle_output
from boss_agent_cli.platforms import get_platform, list_platforms, list_recruiter_platforms

_READONLY_CAPABILITIES = ["search", "detail", "show", "history", "interviews", "recommend", "me", "status"]
_WRITE_CAPABILITIES = ["greet", "apply"]
_LOCAL_CAPABILITIES = ["shortlist", "stats", "config", "schema"]
_CAPABILITY_STATUS_ALIASES = {
	"placeholder_only": "placeholder",
	"low_risk_blocked": "blocked_by_policy",
}

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
		"greet": "low_risk_blocked",
		"apply": "low_risk_blocked",
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
		"greet": "low_risk_blocked",
		"apply": "low_risk_blocked",
	},
	"qiancheng": {
		"search": "not_supported",
		"detail": "not_supported",
		"show": "not_supported",
		"history": "not_supported",
		"interviews": "not_supported",
		"recommend": "not_supported",
		"me": "not_supported",
		"status": "placeholder_only",
		"greet": "not_supported",
		"apply": "not_supported",
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
	"placeholder_only": {
		"label": "仅占位",
		"description": "仅用于平台注册、别名、schema/config 可见性；不代表真实平台能力已接入。",
	},
	"low_risk_blocked": {
		"label": "低风险模式阻断",
		"description": "涉及写操作、敏感数据或平台风险边界；默认低风险模式阻断并提示回到官方页面手动处理。",
	},
}


_PLATFORM_NOTES = {
	"zhipin": "默认平台；候选者侧与招聘者侧注册表均已接入。",
	"zhilian": "候选者侧只读 + 本地辅助链路已对等接入；写操作默认阻断，招聘者侧暂不可用。",
	"qiancheng": "51job/前程无忧当前仅注册平台身份；真实能力返回 NOT_SUPPORTED。",
}

_ALIAS_NAMES = {
	"51job",
}


def _normalize_capability_status(status: str) -> str:
	return _CAPABILITY_STATUS_ALIASES.get(status, status)


def _resolve_platform_filter(platform_name: str | None) -> str | None:
	if platform_name is None:
		return None
	aliases = {"51job": "qiancheng"}
	resolved = aliases.get(platform_name, platform_name)
	candidate_platforms = [name for name in list_platforms() if name not in _ALIAS_NAMES]
	if resolved not in candidate_platforms:
		supported = ", ".join([*candidate_platforms, *sorted(aliases)])
		raise click.BadParameter(
			f"unknown platform {platform_name!r}, supported: {supported}",
			param_hint="--platform",
		)
	return resolved


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
	candidate_platforms = [name for name in list_platforms() if name not in _ALIAS_NAMES]
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
			"status": "placeholder" if name == "qiancheng" else "available",
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
				"status": _normalize_capability_status(raw_status),
				"raw_status": raw_status,
			}
		platforms.append(item)
	capability_filter = None
	if resolved_capability is not None:
		status_groups: dict[str, list[str]] = {
			"available": [],
			"placeholder": [],
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
		"aliases": {"51job": "qiancheng"},
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
@click.option("--platform", "platform_name", default=None, help="仅查看指定平台（支持 qiancheng / 51job 等已注册平台或别名）")
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
