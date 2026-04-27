import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_output, render_message_panel


@click.command("apply")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--lid", default="", help="列表项 ID（可选）")
@click.pass_context
@handle_auth_errors("apply")
def apply_cmd(ctx: click.Context, security_id: str, job_id: str, lid: str) -> None:
	"""发起最小可用投递/立即沟通动作（当前复用立即沟通链路）。"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if cache.is_applied(security_id, job_id):
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

		auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
		with get_platform_instance(ctx, auth) as platform:
			resp = platform.apply(security_id, job_id, lid=lid)
			if not platform.is_success(resp):
				error_code, _ = platform.parse_error(resp)
				handle_error_output(
					ctx,
					"apply",
					code=error_code if error_code != "UNKNOWN" else "NETWORK_ERROR",
					message=resp.get("message") or "投递/立即沟通提交失败",
					recoverable=True,
					recovery_action="重试",
				)
				return
			cache.record_apply(security_id, job_id)

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
