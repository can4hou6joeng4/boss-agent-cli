import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.chat_summary import summarize_messages
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_output, render_message_panel


@click.command("chat-summary")
@click.argument("security_id")
@click.option("--page", default=1, help="页码")
@click.option("--count", default=20, help="每页消息数量")
@click.pass_context
@handle_auth_errors("chat-summary")
def chat_summary_cmd(ctx: click.Context, security_id: str, page: int, count: int) -> None:
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		friends_resp = platform.friend_list(page=1)
		if not platform.is_success(friends_resp):
			code, message = platform.parse_error(friends_resp)
			handle_error_output(
				ctx,
				"chat-summary",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=False,
			)
			return
		friends_data = platform.unwrap_data(friends_resp) or {}
		items = friends_data.get("result") or friends_data.get("friendList") or []

		gid = None
		friend_name = "-"
		for item in items:
			if item.get("securityId") == security_id:
				gid = str(item.get("uid", ""))
				friend_name = item.get("name") or "-"
				break

		if not gid:
			handle_error_output(
				ctx,
				"chat-summary",
				code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}",
			)
			return

		resp = platform.chat_history(gid, security_id, page=page, count=count)
		if not platform.is_success(resp):
			code, message = platform.parse_error(resp)
			handle_error_output(
				ctx,
				"chat-summary",
				code=code,
				message=message or "聊天记录获取失败",
				recoverable=False,
			)
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
