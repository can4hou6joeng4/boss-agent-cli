"""招聘者 — 请求候选人附件简历。"""

from pathlib import Path

import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.recruiter_client import RecruiterAuthError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	error_contract_for_code,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
)
from boss_agent_cli.services.candidate_actions import was_provably_unsent


def _stop_reason(data_dir: Path) -> str | None:
	"""读取招聘自动化停止开关与熔断快照（Issue #2612）。"""
	return AutomationStore.stop_reason_for(data_dir)


@click.command("request-resume")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.pass_context
@handle_auth_errors("recruiter-request-resume")
def request_resume_cmd(ctx: click.Context, friend_id: int) -> None:
	"""请求候选人分享附件简历（issue #217 修复）

	不再需要 --job-id 参数 — 内部从 friend_detail 自动取出 securityId/jobId/name。
	``boss agent stop`` 打开的停止开关会在任何请求之前拦住本命令。
	"""
	if not require_compliance_allowed(ctx, "recruiter-request-resume"):
		return

	data_dir = Path(ctx.obj["data_dir"])
	logger = ctx.obj["logger"]
	base = {"friend_id": friend_id, "requested": False}
	stop = _stop_reason(data_dir)
	if stop is not None:
		recoverable, recovery_action = error_contract_for_code("CIRCUIT_BREAKER_OPEN")
		resume_cmd = hint_command(ctx, "agent run --resume")
		handle_error_output(
			ctx,
			"recruiter-request-resume",
			code="CIRCUIT_BREAKER_OPEN",
			message=f"停止开关已打开（{stop}），本次不会请求附件简历",
			recoverable=recoverable,
			recovery_action=f"核对未确认动作后执行 {resume_cmd} 关闭熔断，再由操作者显式重试；不要自动请求",
			details=base,
			hints={"operator_actions": ["停止开关打开时不要请求附件简历"], "next_actions": [resume_cmd]},
		)
		return

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		# type=4 是抓包实证的"求附件简历"类型；旧代码用 type=3 是错的
		try:
			result = platform.exchange_request_by_friend(friend_id, exchange_type=4)
		except NotImplementedError:
			recoverable, recovery_action = error_contract_for_code("NOT_SUPPORTED")
			handle_error_output(
				ctx,
				"recruiter-request-resume",
				code="NOT_SUPPORTED",
				message="当前平台不支持请求附件简历",
				recoverable=recoverable,
				recovery_action=recovery_action,
				details=base,
			)
			return
		except (AuthRequired, RecruiterAuthError, TokenRefreshFailed, PlatformRiskError, RequestBudgetExceeded):
			# 登录 / 风控 / 预算各有登记表契约（风控是硬边界），交给装饰器统一输出。
			raise
		except Exception as exc:
			# 能证明请求未发出的异常（浏览器通道不可用、调度前停止等）交给装饰器
			# 给出正确的可重试契约；其余异常都可能已经在平台侧生效，不能建议重试
			# （Issue #1840，与 hr location 的未知结果保护同口径）。
			if was_provably_unsent(exc):
				raise
			chatmsg = hint_command(ctx, f"hr chatmsg {friend_id}")
			recoverable, _registered_recovery = error_contract_for_code("ACTION_UNCONFIRMED")
			handle_error_output(
				ctx,
				"recruiter-request-resume",
				code="ACTION_UNCONFIRMED",
				message="附件简历请求是否已发出无法确认，禁止自动重试",
				recoverable=recoverable,
				recovery_action=f"动作可能已生效，请先 {chatmsg} 核实，勿直接重试",
				details={**base, "requested": None},
				hints={
					"next_actions": [chatmsg],
					"operator_actions": ["先在 BOSS 直聘官方页面核对该会话是否已出现附件简历请求，不要直接重试"],
				},
			)
			return
		if not platform.is_success(result):
			handle_platform_error_output(
				ctx, "recruiter-request-resume", platform, result, fallback_message="附件简历请求失败"
			)
			return
		data = {
			"friend_id": friend_id,
			"requested": True,
			"message": "附件简历请求已发送",
		}
		handle_output(
			ctx,
			"recruiter-request-resume",
			data,
			hints={"next_actions": [
				hint_command(ctx, "hr resume <geek_id> --job-id <id> --security-id <id>"),
			]},
		)
