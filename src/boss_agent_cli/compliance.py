"""Capability metadata and legacy operating-mode compatibility."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import click

ASSISTED_MODE = "assisted"
RESEARCH_MODE = "research"
AVAILABLE_OPERATING_MODES = (ASSISTED_MODE, RESEARCH_MODE)

LOW_RISK_MODE_DESCRIPTION = "assisted 模式：保留历史配置名称，所有已实现能力均可调用。"
RESEARCH_MODE_DESCRIPTION = "research 模式：保留历史配置名称，权限与 assisted 模式一致。"

COMPLIANCE_BLOCKED_ACTION = "升级到当前版本后重试；该错误码仅为历史协议兼容保留。"


@dataclass(frozen=True)
class CapabilityPolicy:
	command: str
	allowed_modes: tuple[str, ...]
	risk_class: str
	data_class: str
	requires_explicit_consent: bool
	blocked_reason: str


_POLICY_DEFINITIONS = {
	"greet": ("platform_write", "recruiter_contact", "自动打招呼属于平台写操作。"),
	"batch-greet": ("bulk_outreach", "recruiter_contact", "批量打招呼属于批量触达。"),
	"apply": ("platform_write", "application", "投递/立即沟通属于平台写操作。"),
	"recommend": ("platform_collection", "job_listing", "个性化推荐会自动读取平台推荐流。"),
	"watch-run": ("platform_collection", "job_listing", "增量监控会持续拉取平台职位数据。"),
	"chat": ("personal_data", "communication", "沟通列表涉及会话数据与个人信息。"),
	"exchange": ("personal_data_write", "contact", "联系方式交换涉及个人信息处理。"),
	"mark": ("platform_write", "relationship", "联系人标签涉及平台关系数据写入。"),
	"chatmsg": ("personal_data", "communication", "聊天记录涉及通信内容与个人信息。"),
	"chat-summary": ("personal_data", "communication", "聊天摘要依赖聊天记录与通信内容。"),
	"pipeline": ("personal_data", "candidate_workflow", "候选进度视图依赖平台会话与面试数据。"),
	"follow-up": ("personal_data", "candidate_workflow", "跟进筛选依赖平台会话与面试数据。"),
	"digest": ("personal_data", "candidate_workflow", "日报汇总依赖平台会话与面试数据。"),
	"recruiter-applications": ("personal_data", "application", "投递申请列表涉及候选人个人信息。"),
	"recruiter-candidates": ("platform_collection", "candidate_profile", "候选人搜索涉及个人信息与平台采集。"),
	"recruiter-chat": ("personal_data", "communication", "招聘者沟通列表涉及候选人会话数据。"),
	"recruiter-chatmsg": ("personal_data", "communication", "候选人聊天记录涉及个人信息与通信内容。"),
	"recruiter-last-messages": ("personal_data", "communication", "候选人最近消息摘要涉及通信内容。"),
	"recruiter-resume": ("personal_data", "candidate_profile", "候选人在线简历/联系方式涉及个人信息。"),
	"recruiter-reply": ("platform_write", "communication", "回复候选人属于平台写操作。"),
	"recruiter-request-resume": ("platform_write", "candidate_profile", "请求候选人附件简历涉及个人信息授权。"),
	"crawl": ("platform_collection", "job_listing", "批量采集会读取平台职位列表和详情。"),
	"crawl-cdp": ("browser_debug_protocol", "browser_session_metadata", "采集会启动受隔离的 Chrome 调试会话。"),
	"crawl-hook": ("page_script_injection", "user_provided_script", "Hook 会向页面注入用户提供的脚本。"),
}

_CAPABILITY_POLICIES = {
	command: CapabilityPolicy(
		command=command,
		allowed_modes=AVAILABLE_OPERATING_MODES,
		risk_class=risk_class,
		data_class=data_class,
		requires_explicit_consent=False,
		blocked_reason=blocked_reason,
	)
	for command, (risk_class, data_class, blocked_reason) in _POLICY_DEFINITIONS.items()
}


def capability_policy(command: str) -> CapabilityPolicy | None:
	"""Return the immutable policy for a command, if one is mode-gated."""
	return _CAPABILITY_POLICIES.get(command)


def operating_mode(ctx: click.Context) -> str:
	"""Return the normalized operating mode for the current command context."""
	config = ctx.obj.get("config", {}) if ctx and ctx.obj else {}
	mode = config.get("operating_mode")
	if mode in AVAILABLE_OPERATING_MODES:
		return str(mode)
	return RESEARCH_MODE if config.get("low_risk_mode") is False else ASSISTED_MODE


def restricted_commands(mode: str = ASSISTED_MODE) -> set[str]:
	"""Return commands unavailable in the requested operating mode.

	The parameter and return type are retained for callers that still inspect the
	historical operating-mode surface. Current modes do not restrict commands.
	"""
	return set()


def low_risk_blocked_commands() -> set[str]:
	"""Compatibility alias for assisted-mode restricted commands."""
	return restricted_commands(ASSISTED_MODE)


def is_low_risk_mode(ctx: click.Context) -> bool:
	"""Compatibility predicate for callers using the historical name."""
	return operating_mode(ctx) == ASSISTED_MODE


def require_compliance_allowed(ctx: click.Context, command: str) -> bool:
	"""Compatibility guard that allows every registered capability."""
	return True


def require_capability_mode(mode: str, command: str) -> None:
	"""Compatibility hook for non-CLI callers; modes no longer gate execution."""
	return None


def compliance_mode_data(ctx: click.Context) -> dict[str, Any]:
	"""Expose operating-mode and capability policy data for schema and diagnostics."""
	mode = operating_mode(ctx)
	blocked = restricted_commands(mode)
	return {
		"default_boundary": "open_capabilities",
		"operating_mode": mode,
		"available_modes": list(AVAILABLE_OPERATING_MODES),
		"sensitive_commands_blocked": False,
		"description": LOW_RISK_MODE_DESCRIPTION if mode == ASSISTED_MODE else RESEARCH_MODE_DESCRIPTION,
		"blocked_commands": sorted(blocked),
		"capabilities": {
			command: {**asdict(policy), "allowed_modes": list(policy.allowed_modes)}
			for command, policy in sorted(_CAPABILITY_POLICIES.items())
		},
	}
