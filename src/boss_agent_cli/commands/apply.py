import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, handle_platform_error_output, render_message_panel


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

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_applied(security_id, job_id, account=account):
			handle_error_output(
				ctx,
				"apply",
				code="ALREADY_APPLIED",
				message="已对该职位发起过投递/立即沟通",
				hints={
					"next_actions": [
						boss_command_for_ctx(ctx, "me --section deliver"),
						boss_command_for_ctx(ctx, "chat"),
					]
				},
			)
			return

		with get_platform_instance(ctx, auth) as platform:
			resp = platform.apply(security_id, job_id, lid=lid)
			if not platform.is_success(resp):
				handle_platform_error_output(
					ctx,
					"apply",
					platform,
					resp,
					fallback_message="投递/立即沟通提交失败",
					unknown_error_code="NETWORK_ERROR",
				)
				return
			try:
				cache.record_apply(security_id, job_id, account=account)
			except Exception:
				# 平台已接受投递，本地记账失败不能降级成 NETWORK_ERROR「重试」：
				# 重跑会绕过 is_applied 检查，把同一次投递再提交一遍。
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
				boss_command_for_ctx(ctx, "me --section deliver"),
				boss_command_for_ctx(ctx, "chat"),
			]
		},
	)
