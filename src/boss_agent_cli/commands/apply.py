import click

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.commands._text_guard import reject_unencodable_params
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, handle_platform_error_output, render_message_panel
from boss_agent_cli.services.candidate_actions import (
	CandidateActionBlocked,
	CandidateActionNotRecorded,
	CandidateActionReleaseFailed,
	candidate_action_provably_unsent,
	candidate_release_recovery_action,
	classify_platform_response,
	cli_error_details,
	outcome_unknown_cause,
	run_candidate_action,
	response_provably_unsent,
)


def _hint_command(ctx: click.Context, command: str) -> str:
	"""投递后的记录查询与错误恢复沿用原登录态及数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _emit_already_applied(ctx: click.Context) -> None:
	deliver_cmd = _hint_command(ctx, "me --section deliver")
	chat_cmd = _hint_command(ctx, "chat")
	handle_error_output(
		ctx,
		"apply",
		code="ALREADY_APPLIED",
		message="已对该职位发起过投递/立即沟通",
		recovery_action=f"该职位已投递过：用 {deliver_cmd} 查看投递记录，或用 {chat_cmd} 跟进",
		hints={"next_actions": [deliver_cmd, chat_cmd]},
	)


def _emit_apply_unconfirmed(
	ctx: click.Context, message: str, *, cause: BaseException | None = None
) -> None:
	deliver_cmd = _hint_command(ctx, "me --section deliver")
	chat_cmd = _hint_command(ctx, "chat")
	handle_error_output(
		ctx,
		"apply",
		code="ACTION_UNCONFIRMED",
		message=message,
		recoverable=False,
		recovery_action=f"动作可能已生效：先用 {deliver_cmd} 核对投递记录，不要直接重试",
		details={"outcome_unknown": True, "replay_safe": False, **outcome_unknown_cause(cause)},
		hints={
			"next_actions": [deliver_cmd, chat_cmd],
			"operator_actions": ["先到 BOSS 直聘投递记录页核对本次投递是否已提交；不要自动重发"],
		},
	)


@click.command("apply")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--lid", default="", help="列表项 ID（可选）")
@click.pass_context
@handle_auth_errors("apply")
def apply_cmd(ctx: click.Context, security_id: str, job_id: str, lid: str) -> None:
	"""发起最小可用投递/立即沟通动作（当前复用立即沟通链路）。"""
	if not require_compliance_allowed(ctx, "apply"):
		return

	# apply 是写操作：空标识既不可能命中职位，又会带着空参数真的发出一次投递
	# 请求，失败后看起来像登录或职位失效，真正的原因（argv 少了参数）被掩盖。
	if not security_id.strip() or not job_id.strip():
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"apply",
			code="INVALID_PARAM",
			message=f"security_id 与 job_id 不能为空；先用 {search_cmd} 取到完整职位参数再投递",
			recovery_action=f"补上两个编号后重试；完整职位参数可从 {search_cmd} 的结果中取到",
			hints={"next_actions": [search_cmd]},
		)
		return

	# 标识要进平台请求体：不可编码的字符不可能命中职位，入口按参数错误拒绝，
	# 避免起一次浏览器再失败（Issue #2533）。
	if reject_unencodable_params(ctx, "apply", security_id=security_id, job_id=job_id, lid=lid):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_applied(security_id, job_id, account=account):
			_emit_already_applied(ctx)
			return

		with get_platform_instance(ctx, auth) as platform:
			try:
				resp = run_candidate_action(
					cache,
					security_id=security_id,
					job_id=job_id,
					action="apply",
					account=account,
					send=lambda: platform.apply(security_id, job_id, lid=lid),
					classify=lambda response: classify_platform_response(platform, response),
				)
			except CandidateActionBlocked as blocked:
				# 预检查与真实发送之间，另一个进程可能已经预约 / 完成同一动作。
				if blocked.code == "ALREADY_APPLIED":
					_emit_already_applied(ctx)
				else:
					_emit_apply_unconfirmed(ctx, blocked.message)
				return
			except CandidateActionNotRecorded:
				# 平台已接受投递，本地记账失败不能降级成 NETWORK_ERROR「重试」：
				# 重跑会绕过 is_applied / 预约检查，把同一次投递再提交一遍。
				recoverable, recovery_action = error_contract_for_code("LOCAL_STATE_WRITE_FAILED")
				handle_error_output(
					ctx,
					"apply",
					code="LOCAL_STATE_WRITE_FAILED",
					message="投递/立即沟通已提交，但本地状态保存失败；不要自动重发",
					recoverable=recoverable,
					recovery_action=recovery_action,
					details={
						"security_id": security_id,
						"job_id": job_id,
						"lid": lid,
						"mode": "immediate_chat_apply",
						"applied": True,
						"recorded": False,
					},
					hints={"operator_actions": ["先在 BOSS 直聘官方页面核对投递状态；不要自动重发本次投递"]},
				)
				return
			except CandidateActionReleaseFailed as release_failed:
				# 本次投递未产生平台记录（平台明确拒绝，或请求可证明未发出），
				# 但本地 pending 预约没删掉。直接重试会被预约拦住：报本地状态
				# 错误，恢复指引先清理本地预约（Issues #2944 #2971）。
				recovery = candidate_release_recovery_action(
					release_failed.claim_reference, lambda command: _hint_command(ctx, command)
				)
				handle_error_output(
					ctx,
					"apply",
					code="LOCAL_STATE_WRITE_FAILED",
					message="本次投递未产生平台记录，但本地预约未释放；清理本地预约后再重试",
					recoverable=False,
					recovery_action=recovery,
					details={
						"security_id": security_id,
						"job_id": job_id,
						"lid": lid,
						"request_sent": False,
						"recorded": False,
						"replay_safe": False,
						"release_error": type(release_failed.cause).__name__,
						"claim_reference": release_failed.claim_reference,
					},
					hints={"operator_actions": [recovery]},
				)
				return
			except Exception as exc:
				# 不能证明请求未发出：预约已保留，本次调用必须与下一次调用的口径
				# 一致——报结果未知，而不是可重试的 NETWORK_ERROR（Issue #2297）。
				# 预算 / 时限停止保留各自错误码（request_sent=false，预约已释放），
				# 风控保留 RISK_ERROR_CONTRACTS 的终止语义。
				if (
					not is_request_stop(exc)
					and not isinstance(exc, PlatformRiskError)
					and not candidate_action_provably_unsent(exc)
				):
					_emit_apply_unconfirmed(
						ctx,
						"投递结果未确认；请求可能已生效，请先到平台核对，勿直接重试",
						cause=exc,
					)
					return
				raise

			if not platform.is_success(resp):
				if response_provably_unsent(resp):
					# 浏览器在写出前中断：预约已由 run_candidate_action 释放，按平台
					# 的可重试错误返回，不能报 outcome_unknown 把同一职位锁死（#3105）。
					# 没有证据的 NETWORK_ERROR 仍走下面的未知分支。
					handle_platform_error_output(
						ctx, "apply", platform, resp, fallback_message="投递/立即沟通提交失败"
					)
					return
				error_code, _ = platform.parse_error(resp)
				if error_code in ("ACTION_UNCONFIRMED", "NETWORK_ERROR"):
					# 求职者侧结果未知：核对投递记录与本机会话，而不是招聘者的
					# `boss hr chatmsg`（Issue #990）。NETWORK_ERROR 覆盖 HTTP
					# 5xx / 超时文案：写请求可能已在平台落库，不能给「重试」指引
					# （Issue #2316）。
					deliver_cmd = _hint_command(ctx, "me --section deliver")
					chat_cmd = _hint_command(ctx, "chat")
					handle_error_output(
						ctx, "apply",
						code="ACTION_UNCONFIRMED",
						message="投递结果未确认；请求可能已生效，请先核对，勿直接重试",
						recoverable=False,
						recovery_action=f"动作可能已生效：先用 {deliver_cmd} 核对投递记录，不要直接重试",
						details={"outcome_unknown": True, **cli_error_details(resp)},
						hints={
							"next_actions": [deliver_cmd, chat_cmd],
							"operator_actions": ["先到 BOSS 直聘投递记录页核对本次投递是否已提交；不要自动重发"],
						},
					)
					return
				# UNKNOWN 走登记表的「先核对、勿重试」契约，不能升级成可重试的
				# NETWORK_ERROR：重跑会重复提交同一次投递（Issue #990）。
				handle_platform_error_output(
					ctx,
					"apply",
					platform,
					resp,
					fallback_message="投递/立即沟通提交失败",
				)
				return
			# 成功路径已由 run_candidate_action 统一 settle（旧记录表 + 预约）。

	handle_output(
		ctx,
		"apply",
		{
			"security_id": security_id,
			"job_id": job_id,
			"lid": lid,
			"mode": "immediate_chat_apply",
			"message": "投递/立即沟通已提交",
		},
		render=lambda d: render_message_panel(d, title="apply"),
		hints={
			"next_actions": [
				_hint_command(ctx, "me --section deliver"),
				_hint_command(ctx, "chat"),
			]
		},
	)
