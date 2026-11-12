"""招聘者 — 同意接收候选人附件简历。"""

from typing import Any

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.api.recruiter_resume import ResumeValidationError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	platform_error_contract,
	risk_error_message,
)


@click.command("accept-resume")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.option("--message-id", required=True, type=click.IntRange(min=1), help="hr chatmsg 返回的附件简历请求 mid")
@click.option("--yes", is_flag=True, help="操作者已明确批准同意这条附件简历请求")
@click.option("--dry-run", is_flag=True, help="只预览目标，不请求平台、不修改状态")
@click.pass_context
@handle_auth_errors("recruiter-accept-resume")
def accept_resume_cmd(ctx: click.Context, friend_id: int, message_id: int, yes: bool, dry_run: bool) -> None:
	"""同意接收指定附件简历请求，不主动索要、不下载附件。"""
	if not require_compliance_allowed(ctx, "recruiter-accept-resume"):
		return
	command = "recruiter-accept-resume"
	data: dict[str, Any] = {"friend_id": friend_id, "message_id": message_id, "accepted": False}
	if dry_run:
		handle_output(
			ctx,
			command,
			{**data, "dry_run": True},
			hints={"operator_actions": ["预览未验证请求状态；明确批准该候选人的这条请求后才可加 --yes"]},
		)
		return
	if not yes:
		recoverable, recovery_action = error_contract_for_code("CONFIRMATION_REQUIRED")
		handle_error_output(
			ctx,
			command,
			code="CONFIRMATION_REQUIRED",
			message="尚未获得操作者对这条附件简历请求的明确批准",
			recoverable=recoverable,
			recovery_action=recovery_action,
		)
		return
	auth = AuthManager(ctx.obj["data_dir"], logger=ctx.obj["logger"], platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		auth.get_token()
		try:
			result = platform.accept_resume_by_friend(friend_id, message_id)
		except ResumeValidationError as exc:
			chat_cmd = hint_command(ctx, "hr chat")
			handle_error_output(
				ctx,
				command,
				code="INVALID_PARAM",
				message=str(exc),
				details=data,
				recovery_action=f"用 {chat_cmd} 核对候选人与附件消息 ID 后重试；不确定时不要发送同意请求",
				hints={"next_actions": [chat_cmd]},
			)
			return
		except NotImplementedError:
			handle_error_output(
				ctx, command, code="PLATFORM_NOT_SUPPORTED", message="当前平台不支持同意附件简历请求", details=data
			)
			return
		except (AuthRequired, RecruiterAuthError):
			handle_error_output(
				ctx,
				command,
				code="AUTH_REQUIRED",
				message="登录态不可用，请在官方页面核对后重新登录；不要自动重试",
				recoverable=False,
				recovery_action="重新登录后在官方页面核对本次请求状态，不要自动重试",
				details={**data, "accepted": None},
				hints={"operator_actions": ["登录态不可用，请在官方页面核对后重新登录；不要自动重试"]},
			)
			return
		except TokenRefreshFailed:
			handle_error_output(
				ctx,
				command,
				code="TOKEN_REFRESH_FAILED",
				message="登录态刷新失败，停止本次操作；不要自动重试",
				recoverable=False,
				recovery_action="重新登录后在官方页面核对本次请求状态，不要自动重试",
				details={**data, "accepted": None},
				hints={"operator_actions": ["登录态刷新失败，停止本次操作；不要自动重试"]},
			)
			return
		except PlatformRiskError as exc:
			# 风控异常是多子类结构：EnvironmentRiskError / EnvironmentRiskLockedError
			# 与 AccountRiskError 平级。只捕账号风控会把环境风控漏进下面的兜底，
			# 误报成「结果未知」，也不会给出停止自动化的指引。
			_, recovery_action, risk_hints = platform_error_contract(exc.code)
			hints: dict[str, Any] = dict(risk_hints or {})
			hints["operator_actions"] = ["本次同意请求的结果未知或未发出；先在官方页面核对，不要自动重试"]
			handle_error_output(
				ctx,
				command,
				code=exc.code,
				message=risk_error_message(exc.code),
				recoverable=False,
				recovery_action=recovery_action,
				details={**data, "accepted": None, **(exc.details or {})},
				hints=hints,
			)
			return
		except RequestBudgetExceeded:
			# 预算/时限只在调度前抛出（request_sent=False）：同意请求没有发出，
			# 不能报成「结果未知」让人去核对一个不存在的操作；停止契约由装饰器输出。
			raise
		except Exception:
			# 写请求响应丢失时不能宣称失败或建议重发，也不透传可能含凭据的异常。
			_, recovery_action = error_contract_for_code("RESUME_ACCEPT_RESULT_UNKNOWN")
			handle_error_output(
				ctx,
				command,
				code="RESUME_ACCEPT_RESULT_UNKNOWN",
				message="无法确认同意结果，请在官方页面核对，不要自动重试",
				recoverable=False,
				recovery_action=recovery_action,
				details={**data, "accepted": None},
			)
			return
		if not platform.is_success(result):
			code, _ = platform.parse_error(result)
			code = code if code != "UNKNOWN" else "RESUME_ACCEPT_RESULT_UNKNOWN"
			# 响应字典形态的风控码同样走停止契约；普通失败保留写操作的
			# 「先核对、勿重试」指引，不能按可恢复错误处理。
			_, recovery_action, risk_hints = platform_error_contract(code)
			if risk_hints is None:
				recovery_action = "在官方页面核对请求状态，不要自动重试"
			handle_error_output(
				ctx,
				command,
				code=code,
				message="同意附件简历请求未获成功确认，请在官方页面核对",
				recoverable=False,
				recovery_action=recovery_action,
				details={**data, "accepted": None},
				hints=risk_hints,
			)
			return
		response = platform.unwrap_data(result)
		status = response.get("status") if isinstance(response, dict) else None
		if type(status) is not int or status != 0:
			_, recovery_action = error_contract_for_code("RESUME_ACCEPT_RESULT_UNKNOWN")
			handle_error_output(
				ctx,
				command,
				code="RESUME_ACCEPT_RESULT_UNKNOWN",
				message="平台未确认同意完成，可能需要额外操作；请在官方页面核对",
				recoverable=False,
				recovery_action=recovery_action,
				details={**data, "accepted": None, "status": status if type(status) is int else None},
			)
			return
		handle_output(
			ctx,
			command,
			{**data, "accepted": True},
			hints={"next_actions": [hint_command(ctx, f"hr chatmsg {friend_id}")],
				"operator_actions": ["同意不代表附件已下载，可在该会话里查看后续附件消息"]},
		)
