import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_message_panel

_LABEL_MAP = {
	"新招呼": 1, "沟通中": 2, "已约面": 3, "已获取简历": 4,
	"已交换电话": 5, "已交换微信": 6, "不合适": 7, "牛人发起": 8, "收藏": 11,
}

_LABEL_NAMES = {v: k for k, v in _LABEL_MAP.items()}


def _resolve_label(label_input: str) -> int:
	"""将标签名称或 ID 解析为数字 ID。"""
	if label_input in _LABEL_MAP:
		return _LABEL_MAP[label_input]
	if label_input.isdigit():
		return int(label_input)
	for name, lid in _LABEL_MAP.items():
		if label_input in name:
			return lid
	raise click.BadParameter(
		f"未知标签: {label_input}。可用: {', '.join(_LABEL_MAP.keys())}"
	)


@click.command("mark")
@click.argument("security_id")
@click.option("--label", required=True, help="标签名称（新招呼/沟通中/已约面/已获取简历/已交换电话/已交换微信/不合适/收藏）或 ID")
@click.option("--remove", is_flag=True, default=False, help="移除标签（默认为添加）")
@click.pass_context
@handle_auth_errors("mark")
def mark_cmd(ctx: click.Context, security_id: str, label: str, remove: bool) -> None:
	"""给联系人添加/移除标签"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger)

	label_id = _resolve_label(label)
	label_name = _LABEL_NAMES.get(label_id, str(label_id))
	action_text = "移除" if remove else "添加"

	with get_platform_instance(ctx, auth) as platform:
		friends_resp = platform.friend_list(page=1)
		zp_data = friends_resp.get("zpData", {})
		items = zp_data.get("result") or zp_data.get("friendList") or []

		friend_id = None
		friend_source = 0
		friend_name = None
		for item in items:
			if item.get("securityId") == security_id:
				friend_id = str(item.get("uid", ""))
				friend_source = item.get("friendSource", 0)
				friend_name = item.get("name") or "-"
				break

		if not friend_id:
			handle_error_output(
				ctx, "mark", code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}",
			)
			return

		platform.friend_label(friend_id, label_id, friend_source, remove=remove)

		data = {
			"security_id": security_id,
			"name": friend_name,
			"label": label_name,
			"action": action_text,
			"message": f"已{action_text}标签「{label_name}」",
		}
		handle_output(
			ctx, "mark", data,
			render=lambda d: render_message_panel(d, title="mark"),
			hints={"next_actions": [
				"boss chat — 返回沟通列表",
			]},
		)
