import datetime
import re
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_simple_list

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"

_MSG_TYPE_MAP = {
	1: "文本", 2: "图片", 3: "招呼", 4: "简历", 5: "系统",
	6: "名片", 7: "语音", 8: "视频", 9: "表情",
}


@click.command("chatmsg")
@click.argument("security_id")
@click.option("--page", default=1, help="页码")
@click.option("--count", default=20, help="每页消息数量")
@click.option("--raw", "show_raw", is_flag=True, default=False, help="输出保真结构化消息字段（仍受合规门控）")
@click.pass_context
@handle_auth_errors("chatmsg")
def chatmsg_cmd(ctx: click.Context, security_id: str, page: int, count: int, show_raw: bool) -> None:
	"""查看与指定好友的聊天消息历史"""
	if not require_compliance_allowed(ctx, "chatmsg"):
		ctx.exit(1)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		try:
			friend_item, friends_error = find_friend_by_security_id(platform, security_id)
		except NotImplementedError as exc:
			handle_error_output(
				ctx, "chatmsg",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持沟通列表能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		except FriendLookupLimitExceeded as exc:
			handle_error_output(
				ctx, "chatmsg",
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
				ctx, "chatmsg",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		if friend_item is None:
			handle_error_output(
				ctx, "chatmsg",
				code="JOB_NOT_FOUND",
				message=f"未在沟通列表中找到 security_id={security_id}，请确认该联系人存在",
			)
			return
		gid = str(friend_item.get("uid", ""))
		friend_name = friend_item.get("name") or "-"

		try:
			resp = platform.chat_history(gid, security_id, page=page, count=count)
		except NotImplementedError as exc:
			handle_error_output(
				ctx, "chatmsg",
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
				ctx, "chatmsg",
				code=code,
				message=message or "聊天记录获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		msg_data = platform.unwrap_data(resp) or {}
		messages = msg_data.get("messages") or msg_data.get("historyMsgList") or []

		result = []
		for msg in messages:
			result.append(_normalize_message(msg, gid=gid, friend_name=friend_name, include_raw=show_raw))

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
				f"{boss_command_for_ctx(ctx, 'chat')} — 返回沟通列表",
				f"{boss_command_for_ctx(ctx, f'detail {security_id}')} — 查看职位详情",
			]},
		)


def _normalize_message(
	msg: dict[str, Any],
	*,
	gid: str,
	friend_name: str,
	include_raw: bool = False,
) -> dict[str, Any]:
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

	body = _message_body(msg)
	text = msg.get("text") or body.get("text", "") or ""
	msg_type = msg.get("type")
	normalized: dict[str, Any] = {
		"from": from_name,
		"type": _message_type_label(msg_type),
		"text": text,
		"time": msg_time,
	}
	if include_raw:
		normalized.update({
			"message_id": msg.get("id") or msg.get("msgId") or msg.get("messageId"),
			"type_code": msg.get("type"),
			"timestamp": msg.get("time"),
			"body": body,
			"links": _extract_links(msg, body=body),
			"security_id": _first_present(msg, body, ("securityId", "security_id")),
			"job_id": _first_present(msg, body, ("jobId", "job_id", "encryptJobId")),
			"raw": msg,
		})
	return normalized


def _message_body(msg: dict[str, Any]) -> dict[str, Any]:
	body = msg.get("body")
	if isinstance(body, dict):
		return body
	return {}


def _message_type_label(msg_type: Any) -> str:
	if isinstance(msg_type, int):
		return _MSG_TYPE_MAP.get(msg_type, f"其他({msg_type})")
	return f"其他({msg_type})"


def _first_present(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> Any:
	for source in (primary, secondary):
		for key in keys:
			value = source.get(key)
			if value not in (None, ""):
				return value
	return None


def _extract_links(msg: dict[str, Any], *, body: dict[str, Any]) -> list[str]:
	links: list[str] = []
	for value in list(msg.values()) + list(body.values()):
		if isinstance(value, str):
			links.extend(re.findall(r"https?://[^\s\"'<>]+", value))
		elif isinstance(value, dict):
			links.extend(_extract_links(value, body={}))
		elif isinstance(value, list):
			for item in value:
				if isinstance(item, str):
					links.extend(re.findall(r"https?://[^\s\"'<>]+", item))
				elif isinstance(item, dict):
					links.extend(_extract_links(item, body={}))
	return list(dict.fromkeys(links))
