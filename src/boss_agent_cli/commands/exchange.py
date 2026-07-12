import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands.contact_lookup import resolve_friend_or_emit
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_message_panel


@click.command("exchange")
@click.argument("security_id")
@click.option("--type", "exchange_type", default="phone", type=click.Choice(["phone", "wechat"]), help="交换类型：phone=手机号 / wechat=微信")
@click.pass_context
@handle_auth_errors("exchange")
def exchange_cmd(ctx: click.Context, security_id: str, exchange_type: str) -> None:
	"""请求交换联系方式（手机号或微信）"""
	if not require_compliance_allowed(ctx, "exchange"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	type_id = 2 if exchange_type == "wechat" else 1
	type_label = "微信" if exchange_type == "wechat" else "手机号"

	with get_platform_instance(ctx, auth) as platform:
		friend_item = resolve_friend_or_emit(ctx, "exchange", platform, security_id)
		if friend_item is None:
			return
		uid = str(friend_item.get("uid", ""))
		friend_name: str = friend_item.get("name") or "-"

		try:
			resp = platform.exchange_contact(security_id, uid, friend_name, exchange_type=type_id)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "exchange", exc, fallback_message=f"当前平台不支持{type_label}交换能力")
			return
		if not platform.is_success(resp):
			handle_platform_error_output(ctx, "exchange", platform, resp, fallback_message=f"{type_label}交换请求失败")
			return

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
				f"{boss_command_for_ctx(ctx, 'chat')} — 返回沟通列表",
				f"{boss_command_for_ctx(ctx, f'chatmsg {security_id}')} — 查看聊天记录",
			]},
		)
