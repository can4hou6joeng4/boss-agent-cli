import datetime
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_simple_list

_MSG_TYPE_MAP = {
	1: "文本", 2: "图片", 3: "招呼", 4: "简历", 5: "系统",
	6: "名片", 7: "语音", 8: "视频", 9: "表情",
}


@click.command("chatmsg")
@click.argument("security_id")
@click.option("--page", default=1, help="页码")
@click.option("--count", default=20, help="每页消息数量")
@click.pass_context
@handle_auth_errors("chatmsg")
def chatmsg_cmd(ctx: click.Context, security_id: str, page: int, count: int) -> None:
	"""查看与指定好友的聊天消息历史"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger)

	with get_platform_instance(ctx, auth) as platform:
		friends_resp = platform.friend_list(page=1)
		zp_data = friends_resp.get("zpData", {})
		items = zp_data.get("result") or zp_data.get("friendList") or []

		gid = None
		friend_name = None
		for item in items:
			if item.get("securityId") == security_id:
				gid = str(item.get("uid", ""))
				friend_name = item.get("name") or "-"
				break

		if not gid:
			handle_error_output(
				ctx, "chatmsg",
				code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}，请确认该联系人存在",
			)
			return

		resp = platform.chat_history(gid, security_id, page=page, count=count)
		msg_data = resp.get("zpData", {})
		messages = msg_data.get("messages") or msg_data.get("historyMsgList") or []

		result = []
		for msg in messages:
			from_obj = msg.get("from", {})
			is_self = False
			from_name = friend_name
			if isinstance(from_obj, dict):
				is_self = str(from_obj.get("uid", "")) != gid
				if not is_self:
					from_name = from_obj.get("name", friend_name)
			if is_self:
				from_name = "我"

			msg_time = ""
			if ts := msg.get("time"):
				msg_time = datetime.datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")

			result.append({
				"from": from_name,
				"type": _MSG_TYPE_MAP.get(msg.get("type"), f"其他({msg.get('type')})"),
				"text": msg.get("text") or msg.get("body", {}).get("text", "") or "",
				"time": msg_time,
			})

		def _render(data: list[dict[str, Any]]) -> None:
			render_simple_list(
				data,
				f"聊天记录 — {friend_name}",
				[
					("发送方", "from", "bold cyan"),
					("类型", "type", "dim"),
					("内容", "text", "yellow"),
					("时间", "time", "dim"),
				],
			)

		handle_output(
			ctx, "chatmsg", result,
			render=_render,
			hints={"next_actions": [
				"boss chat — 返回沟通列表",
				f"boss detail {security_id} — 查看职位详情",
			]},
		)
