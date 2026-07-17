"""Operating-mode policies for platform-sensitive commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import click

from boss_agent_cli.display import handle_error_output

ASSISTED_MODE = "assisted"
RESEARCH_MODE = "research"
AVAILABLE_OPERATING_MODES = (ASSISTED_MODE, RESEARCH_MODE)

LOW_RISK_MODE_DESCRIPTION = (
	"默认低风险模式（assisted）：本地辅助、只读优先、用户主动触发，不自动触达、不批量处理候选人个人数据。"
)
RESEARCH_MODE_DESCRIPTION = (
	"研究模式：显式启用浏览器协议、反调试、风控适配和受控采集能力；仍要求有限运行、脱敏和可停止。"
)

COMPLIANCE_BLOCKED_ACTION = (
	"保持默认 assisted 模式并回到平台官网手动完成；如需研究能力，请显式设置 operating_mode=research。"
)
_COMPLIANCE_NEXT_ACTIONS = [
	"使用只读命令确认信息，例如 boss search、boss detail、boss show、boss shortlist",
	"仅在理解账号、数据和平台风险后显式运行 boss config set operating_mode research",
]
_COMPLIANCE_BLOCK_HINTS = {
	"policy": "low_risk_assistance",
	"blocked": True,
	"manual_action_required": True,
	"allowed_alternatives": ["search", "detail", "show", "shortlist"],
	"next_actions": _COMPLIANCE_NEXT_ACTIONS,
}


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
		allowed_modes=(RESEARCH_MODE,),
		risk_class=risk_class,
		data_class=data_class,
		requires_explicit_consent=True,
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
	"""Return commands unavailable in the requested operating mode."""
	return {
		command
		for command, policy in _CAPABILITY_POLICIES.items()
		if mode not in policy.allowed_modes
	}


def low_risk_blocked_commands() -> set[str]:
	"""Compatibility alias for assisted-mode restricted commands."""
	return restricted_commands(ASSISTED_MODE)


def is_low_risk_mode(ctx: click.Context) -> bool:
	"""Compatibility predicate for callers using the historical name."""
	return operating_mode(ctx) == ASSISTED_MODE


def require_compliance_allowed(ctx: click.Context, command: str) -> bool:
	"""Emit a standard error when the active mode does not allow a command."""
	policy = capability_policy(command)
	mode = operating_mode(ctx)
	if policy is None or mode in policy.allowed_modes:
		return True

	handle_error_output(
		ctx,
		command,
		code="COMPLIANCE_BLOCKED",
		message=f"{policy.blocked_reason} {LOW_RISK_MODE_DESCRIPTION}",
		recoverable=False,
		recovery_action=COMPLIANCE_BLOCKED_ACTION,
		hints={**_COMPLIANCE_BLOCK_HINTS, "required_mode": RESEARCH_MODE},
	)
	return False


def require_capability_mode(mode: str, command: str) -> None:
	"""Raise a compact error when a non-CLI caller uses a blocked capability."""
	policy = capability_policy(command)
	if policy is not None and mode not in policy.allowed_modes:
		raise ValueError(f"{command} 仅可在显式 operating_mode=research 下运行")


def compliance_mode_data(ctx: click.Context) -> dict[str, Any]:
	"""Expose operating-mode and capability policy data for schema and diagnostics."""
	mode = operating_mode(ctx)
	blocked = restricted_commands(mode)
	return {
		"default_boundary": "low_risk_assistance",
		"operating_mode": mode,
		"available_modes": list(AVAILABLE_OPERATING_MODES),
		"sensitive_commands_blocked": bool(blocked),
		"description": LOW_RISK_MODE_DESCRIPTION if mode == ASSISTED_MODE else RESEARCH_MODE_DESCRIPTION,
		"blocked_commands": sorted(blocked),
		"capabilities": {
			command: {**asdict(policy), "allowed_modes": list(policy.allowed_modes)}
			for command, policy in sorted(_CAPABILITY_POLICIES.items())
		},
	}
