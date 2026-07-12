import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.chat_summary import summarize_messages
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands.contact_lookup import resolve_friend_or_emit
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_message_panel


@click.command("chat-summary")
@click.argument("security_id")
@click.option("--page", default=1, help="页码")
@click.option("--count", default=20, help="每页消息数量")
@click.pass_context
@handle_auth_errors("chat-summary")
def chat_summary_cmd(ctx: click.Context, security_id: str, page: int, count: int) -> None:
	if not require_compliance_allowed(ctx, "chat-summary"):
		ctx.exit(1)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		friend_item = resolve_friend_or_emit(ctx, "chat-summary", platform, security_id)
		if friend_item is None:
			return
		gid = str(friend_item.get("uid", ""))
		friend_name = friend_item.get("name") or "-"

		try:
			resp = platform.chat_history(gid, security_id, page=page, count=count)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "chat-summary", exc, fallback_message="当前平台不支持聊天记录能力")
			return
		if not platform.is_success(resp):
			handle_platform_error_output(ctx, "chat-summary", platform, resp, fallback_message="聊天记录获取失败")
			return
		msg_data = platform.unwrap_data(resp) or {}
		messages = msg_data.get("messages") or msg_data.get("historyMsgList") or []
		summary = summarize_messages(messages, friend_uid=gid)

	handle_output(
		ctx,
		"chat-summary",
		{
			"security_id": security_id,
			"name": friend_name,
			**summary,
		},
		render=lambda d: render_message_panel(d, title="chat-summary"),
		hints={
			"next_actions": [
				boss_command_for_ctx(ctx, "chat"),
				boss_command_for_ctx(ctx, f"chatmsg {security_id}"),
			]
		},
	)
