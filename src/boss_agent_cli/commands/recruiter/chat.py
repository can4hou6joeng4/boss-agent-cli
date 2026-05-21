"""招聘者 — 候选人沟通。"""
import datetime
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.chat_utils import MSG_STATUS_LABELS
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output


_RECRUITER_MSG_STATUS_LABELS = {0: "发送中", **MSG_STATUS_LABELS}


def _format_chat_time(value: Any) -> str:
	if value in (None, ""):
		return "-"
	if isinstance(value, int | float):
		return datetime.datetime.fromtimestamp(value / 1000).strftime("%m-%d %H:%M")
	return str(value)


def _friend_id_for(item: dict[str, Any]) -> int | None:
	for key in ("friendId", "friend_id", "uid", "gid"):
		value = item.get(key)
		if value in (None, ""):
			continue
		try:
			return int(str(value))
		except (TypeError, ValueError):
			continue
	return None


def _list_dict_items(value: Any) -> list[dict[str, Any]]:
	if isinstance(value, list):
		return [item for item in value if isinstance(item, dict)]
	return []


def _friend_data(data: Any) -> dict[str, Any]:
	if isinstance(data, dict):
		return data
	if isinstance(data, list):
		return {"friendList": _list_dict_items(data)}
	return {}


def _message_items(data: Any) -> list[dict[str, Any]]:
	if isinstance(data, list):
		return _list_dict_items(data)
	if not isinstance(data, dict):
		return []
	for key in ("lastMessageList", "messages", "messageList", "result", "friendList", "list"):
		value = data.get(key)
		if isinstance(value, list):
			return _list_dict_items(value)
	return []


def _message_status_label(item: dict[str, Any]) -> str:
	status = item.get("msg_status") or item.get("status")
	info = item.get("lastMessageInfo")
	if isinstance(info, dict) and info.get("status") not in (None, ""):
		status = info.get("status")
	if isinstance(status, str):
		return status
	if not isinstance(status, int):
		return "未知"
	return _RECRUITER_MSG_STATUS_LABELS.get(status, "未知")


def _message_text(item: dict[str, Any]) -> str:
	for key in ("last_msg", "lastMsg", "content", "text", "message", "msgContent"):
		value = item.get(key)
		if value not in (None, ""):
			return str(value)
	body = item.get("body")
	if isinstance(body, dict) and body.get("text") not in (None, ""):
		return str(body["text"])
	return "-"


def _normalize_last_message(item: dict[str, Any]) -> dict[str, Any]:
	friend_id = _friend_id_for(item)
	return {
		"friendId": friend_id,
		"unread": item.get("unread") if item.get("unread") is not None else item.get("unreadMsgCount") or item.get("newMsgCount") or 0,
		"msg_status": _message_status_label(item),
		"last_msg": _message_text(item),
		"last_time": _format_chat_time(
			item.get("last_time") or item.get("lastTime") or item.get("lastTS") or item.get("time") or item.get("timestamp")
		),
	}


def _merge_last_messages(friend_items: list[dict[str, Any]], message_items: list[dict[str, Any]]) -> None:
	messages_by_friend = {
		friend_id: _normalize_last_message(item)
		for item in message_items
		if (friend_id := _friend_id_for(item)) is not None
	}
	for item in friend_items:
		friend_id = _friend_id_for(item)
		message = messages_by_friend.get(friend_id) if friend_id is not None else None
		if message is None:
			message = _normalize_last_message(item)
		item.update({key: value for key, value in message.items() if key != "friendId"})


def _friend_items(data: Any) -> list[dict[str, Any]]:
	if isinstance(data, list):
		return _list_dict_items(data)
	if not isinstance(data, dict):
		return []
	for key in ("friendList", "result", "list"):
		value = data.get(key)
		if isinstance(value, list):
			return _list_dict_items(value)
	return []


def _friend_ids_from_items(items: list[dict[str, Any]]) -> list[int]:
	ids: list[int] = []
	for item in items:
		friend_id = _friend_id_for(item)
		if friend_id is not None and friend_id not in ids:
			ids.append(friend_id)
	return ids


def _fetch_friend_ids(platform: Any, *, page: int, label_id: int, job_id: str | None) -> tuple[list[int], dict[str, Any] | None]:
	result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
	if not platform.is_success(result):
		return [], result
	data = _friend_data(platform.unwrap_data(result))
	return _friend_ids_from_items(_friend_items(data)), None


@click.command("chat")
@click.option("--page", default=1, type=int, help="页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=int, help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.pass_context
@handle_auth_errors("recruiter-chat")
def recruiter_chat_cmd(ctx: click.Context, page: int, job_id: str | None, label_id: int) -> None:
	"""查看与候选人的沟通列表"""
	if not require_compliance_allowed(ctx, "recruiter-chat"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-chat",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = _friend_data(platform.unwrap_data(result))
		friend_items = _friend_items(data)
		friend_ids = _friend_ids_from_items(friend_items)
		if friend_ids:
			try:
				last_messages = platform.last_messages(friend_ids)
			except NotImplementedError:
				last_messages = None
			if last_messages is not None and platform.is_success(last_messages):
				last_data = platform.unwrap_data(last_messages) or {}
				_merge_last_messages(friend_items, _message_items(last_data))
			else:
				_merge_last_messages(friend_items, [])
		handle_output(
			ctx, "recruiter-chat", data,
			hints={"next_actions": [
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历",
				"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
			]},
		)


@click.command("chatmsg")
@click.argument("friend_id", type=int)
@click.option("--count", default=20, type=int, help="消息数量")
@click.option("--max-msg-id", default=None, type=int, help="向前翻页的最大消息 ID")
@click.pass_context
@handle_auth_errors("recruiter-chatmsg")
def recruiter_chatmsg_cmd(ctx: click.Context, friend_id: int, count: int, max_msg_id: int | None) -> None:
	"""查看与指定候选人的聊天消息历史"""
	if not require_compliance_allowed(ctx, "recruiter-chatmsg"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.chat_history(friend_id, count=count, max_msg_id=max_msg_id)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-chatmsg",
				code=code,
				message=message or "聊天记录获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = platform.unwrap_data(result) or {}
		handle_output(
			ctx, "recruiter-chatmsg", data,
			hints={"next_actions": [
				f"boss hr reply {friend_id} <message> — 回复候选人消息",
				"boss hr chat — 返回沟通列表",
			]},
		)


@click.command("last-messages")
@click.option("--page", default=1, type=int, help="沟通列表页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=int, help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.option("--friend-id", "friend_ids", multiple=True, type=int, help="指定候选人会话 friend_id，可重复")
@click.pass_context
@handle_auth_errors("recruiter-last-messages")
def recruiter_last_messages_cmd(
	ctx: click.Context,
	page: int,
	job_id: str | None,
	label_id: int,
	friend_ids: tuple[int, ...],
) -> None:
	"""批量查看候选人最近消息摘要"""
	if not require_compliance_allowed(ctx, "recruiter-last-messages"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		ids = list(dict.fromkeys(friend_ids))
		if not ids:
			ids, error = _fetch_friend_ids(platform, page=page, label_id=label_id, job_id=job_id)
			if error is not None:
				code, message = platform.parse_error(error)
				recoverable, recovery_action = error_contract_for_code(code)
				handle_error_output(
					ctx, "recruiter-last-messages",
					code=code,
					message=message or "沟通列表获取失败",
					recoverable=recoverable,
					recovery_action=recovery_action,
				)
				return
		if not ids:
			handle_output(ctx, "recruiter-last-messages", {"friend_ids": [], "messages": []})
			return

		result = platform.last_messages(ids)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-last-messages",
				code=code,
				message=message or "最近消息获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = platform.unwrap_data(result) or {}
		messages = [_normalize_last_message(item) for item in _message_items(data)]
		handle_output(
			ctx, "recruiter-last-messages",
			{"friend_ids": ids, "messages": messages},
			hints={"next_actions": [
				"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
				"boss hr reply <friend_id> <message> — 回复候选人消息",
			]},
		)
