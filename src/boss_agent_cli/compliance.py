"""Compliance guardrails for platform-sensitive commands."""

from __future__ import annotations

from typing import Any

import click

from boss_agent_cli.display import handle_error_output

LOW_RISK_MODE_DESCRIPTION = (
	"低风险辅助模式：本地辅助、只读优先、用户主动触发、不规避风控、不批量触达、不抓取平台数据。"
)

COMPLIANCE_BLOCKED_ACTION = "保持默认低风险模式；如需处理，请回到平台官网手动完成。"

_SENSITIVE_COMMANDS = {
	"greet": "自动打招呼属于平台写操作，默认低风险模式不通过 CLI 触达招聘者。",
	"batch-greet": "批量打招呼属于批量触达，默认低风险模式不执行。",
	"apply": "投递/立即沟通属于平台写操作，默认低风险模式不通过 CLI 提交。",
	"recommend": "个性化推荐会自动读取平台推荐流，默认低风险模式不抓取平台数据。",
	"watch-run": "增量监控会自动批量拉取平台职位数据，默认低风险模式不执行。",
	"chat": "沟通列表涉及会话数据与个人信息，默认低风险模式不读取。",
	"exchange": "联系方式交换涉及个人信息处理，默认低风险模式不通过 CLI 发起。",
	"mark": "联系人标签涉及平台关系数据写入，默认低风险模式不通过 CLI 修改。",
	"chatmsg": "聊天记录涉及通信内容与个人信息，默认低风险模式不读取。",
	"chat-summary": "聊天摘要依赖聊天记录与通信内容，默认低风险模式不生成。",
	"pipeline": "候选进度视图依赖平台会话与面试数据，默认低风险模式不聚合。",
	"follow-up": "跟进筛选依赖平台会话与面试数据，默认低风险模式不聚合。",
	"digest": "日报汇总依赖平台会话与面试数据，默认低风险模式不聚合。",
	"recruiter-applications": "投递申请列表涉及候选人个人信息，默认低风险模式不读取。",
	"recruiter-candidates": "候选人搜索涉及候选人个人信息与平台数据抓取，默认低风险模式不执行。",
	"recruiter-chat": "招聘者沟通列表涉及候选人会话数据，默认低风险模式不读取。",
	"recruiter-chatmsg": "候选人聊天记录涉及个人信息与通信内容，默认低风险模式不读取。",
	"recruiter-last-messages": "候选人最近消息摘要涉及通信内容，默认低风险模式不读取。",
	"recruiter-resume": "候选人在线简历/联系方式交换涉及个人信息，默认低风险模式不处理。",
	"recruiter-reply": "回复候选人属于平台写操作，默认低风险模式不发送。",
	"recruiter-request-resume": "请求候选人附件简历涉及个人信息授权，默认低风险模式不发起。",
}


def is_low_risk_mode(ctx: click.Context) -> bool:
	"""Return whether platform-sensitive commands should be blocked by default."""
	config = ctx.obj.get("config", {}) if ctx and ctx.obj else {}
	return bool(config.get("low_risk_mode", True))


def require_compliance_allowed(ctx: click.Context, command: str) -> bool:
	"""Emit a standard error and return False when a sensitive command is blocked."""
	if not is_low_risk_mode(ctx):
		return True

	reason = _SENSITIVE_COMMANDS.get(command)
	if reason is None:
		return True

	handle_error_output(
		ctx,
		command,
		code="COMPLIANCE_BLOCKED",
		message=f"{reason} {LOW_RISK_MODE_DESCRIPTION}",
		recoverable=False,
		recovery_action=COMPLIANCE_BLOCKED_ACTION,
		hints={
			"next_actions": [
				"使用只读命令确认信息，例如 boss search、boss detail、boss show、boss shortlist",
				"需要写操作或候选人个人信息处理时，请回到平台官网由用户手动完成",
			],
		},
	)
	return False


def compliance_mode_data(ctx: click.Context) -> dict[str, Any]:
	"""Expose guardrail status for schema and diagnostics."""
	return {
		"default_boundary": "low_risk_assistance",
		"sensitive_commands_blocked": is_low_risk_mode(ctx),
		"description": LOW_RISK_MODE_DESCRIPTION,
		"blocked_commands": sorted(_SENSITIVE_COMMANDS),
	}
