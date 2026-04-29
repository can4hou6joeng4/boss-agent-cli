import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_message_panel

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


@click.command("exchange")
@click.argument("security_id")
@click.option("--type", "exchange_type", default="phone", type=click.Choice(["phone", "wechat"]), help="交换类型：phone=手机号 / wechat=微信")
@click.pass_context
@handle_auth_errors("exchange")
def exchange_cmd(ctx: click.Context, security_id: str, exchange_type: str) -> None:
	"""请求交换联系方式（手机号或微信）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	type_id = 2 if exchange_type == "wechat" else 1
	type_label = "微信" if exchange_type == "wechat" else "手机号"

	with get_platform_instance(ctx, auth) as platform:
		try:
			friend_item, friends_error = find_friend_by_security_id(platform, security_id)
		except NotImplementedError as exc:
			handle_error_output(
				ctx, "exchange",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持沟通列表能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		except FriendLookupLimitExceeded as exc:
			handle_error_output(
				ctx, "exchange",
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
				ctx, "exchange",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		if friend_item is None:
			handle_error_output(
				ctx, "exchange", code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}",
			)
			return
		uid = str(friend_item.get("uid", ""))
		friend_name: str = friend_item.get("name") or "-"

		try:
			resp = platform.exchange_contact(security_id, uid, friend_name, exchange_type=type_id)
		except NotImplementedError as exc:
			handle_error_output(
				ctx, "exchange",
				code="NOT_SUPPORTED",
				message=str(exc) or f"当前平台不支持{type_label}交换能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		if not platform.is_success(resp):
			code, message = platform.parse_error(resp)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "exchange",
				code=code,
				message=message or f"{type_label}交换请求失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
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
