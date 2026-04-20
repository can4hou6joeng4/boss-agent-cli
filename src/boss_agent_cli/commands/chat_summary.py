import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.chat_summary import summarize_messages
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_message_panel


@click.command("chat-summary")
@click.argument("security_id")
@click.option("--page", default=1, help="页码")
@click.option("--count", default=20, help="每页消息数量")
@click.pass_context
@handle_auth_errors("chat-summary")
def chat_summary_cmd(ctx: click.Context, security_id: str, page: int, count: int) -> None:
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		friends_resp = client.friend_list(page=1)
		items = friends_resp.get("zpData", {}).get("result") or friends_resp.get("zpData", {}).get("friendList") or []

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

		resp = client.chat_history(gid, security_id, page=page, count=count)
		messages = resp.get("zpData", {}).get("messages") or resp.get("zpData", {}).get("historyMsgList") or []
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
		hints={"next_actions": ["boss chat", f"boss chatmsg {security_id}"]},
	)
