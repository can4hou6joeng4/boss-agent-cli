import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.chat_summary import summarize_messages
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_message_panel

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


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
		try:
			friend_item, friends_error = find_friend_by_security_id(platform, security_id)
		except NotImplementedError as exc:
			handle_error_output(
				ctx,
				"chat-summary",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持沟通列表能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		except FriendLookupLimitExceeded as exc:
			handle_error_output(
				ctx,
				"chat-summary",
				code="NETWORK_ERROR",
				message=str(exc),
				recoverable=True,
				recovery_action="重试",
			)
			return
		if friends_error is not None:
			code, message = platform.parse_error(friends_error)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx,
				"chat-summary",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		if friend_item is None:
			handle_error_output(
				ctx,
				"chat-summary",
				code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}",
			)
			return
		gid = str(friend_item.get("uid", ""))
		friend_name = friend_item.get("name") or "-"

		try:
			resp = platform.chat_history(gid, security_id, page=page, count=count)
		except NotImplementedError as exc:
			handle_error_output(
				ctx,
				"chat-summary",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持聊天记录能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		if not platform.is_success(resp):
			code, message = platform.parse_error(resp)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx,
				"chat-summary",
				code=code,
				message=message or "聊天记录获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
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
