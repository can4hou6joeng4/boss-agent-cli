import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_message_panel


@click.command("exchange")
@click.argument("security_id")
@click.option("--type", "exchange_type", default="phone", type=click.Choice(["phone", "wechat"]), help="交换类型：phone=手机号 / wechat=微信")
@click.pass_context
@handle_auth_errors("exchange")
def exchange_cmd(ctx: click.Context, security_id: str, exchange_type: str) -> None:
	"""请求交换联系方式（手机号或微信）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	type_id = 2 if exchange_type == "wechat" else 1
	type_label = "微信" if exchange_type == "wechat" else "手机号"

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		friends_resp = client.friend_list(page=1)
		zp_data = friends_resp.get("zpData", {})
		items = zp_data.get("result") or zp_data.get("friendList") or []

		uid = None
		friend_name: str = "-"
		for item in items:
			if item.get("securityId") == security_id:
				uid = str(item.get("uid", ""))
				friend_name = item.get("name") or "-"
				break

		if not uid:
			handle_error_output(
				ctx, "exchange", code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}",
			)
			return

		client.exchange_contact(security_id, uid, friend_name, exchange_type=type_id)

		data = {
			"security_id": security_id,
			"name": friend_name,
			"type": type_label,
			"message": f"已向 {friend_name} 发送{type_label}交换请求",
		}
		handle_output(
			ctx, "exchange", data,
			render=lambda d: render_message_panel(d, title="exchange"),
			hints={"next_actions": [
				"boss chat — 返回沟通列表",
				f"boss chatmsg {security_id} — 查看聊天记录",
			]},
		)
