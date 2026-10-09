"""招聘者 — 候选人沟通。"""
import datetime
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.services.chat_utils import MSG_STATUS_LABELS
from boss_agent_cli.display import handle_auth_errors, handle_output, handle_platform_error_output


_RECRUITER_MSG_STATUS_LABELS = {0: "发送中", **MSG_STATUS_LABELS}


def _format_chat_time(value: Any) -> str | None:
	"""毫秒时间戳格式化为本地 MM-DD HH:MM；平台给的字符串原样返回；没有就返回 None。"""
	if value in (None, "") or isinstance(value, bool):
		return None
	if isinstance(value, int | float):
		if value <= 0:
			return None
		return datetime.datetime.fromtimestamp(value / 1000).strftime("%m-%d %H:%M")
	text = str(value).strip()
	if text.isdigit():
		return _format_chat_time(int(text))
	return text or None


def _as_int(value: Any) -> int | None:
	if value in (None, "") or isinstance(value, bool):
		return None
	try:
		return int(str(value))
	except (TypeError, ValueError):
		return None


def _friend_id_for(item: dict[str, Any], known_ids: set[int] | None = None) -> int | None:
	for key in ("friendId", "friend_id", "uid", "gid"):
		value = _as_int(item.get(key))
		if value is not None:
			return value
	# userLastMsg 可能直接返回消息对象（from/to 两端 uid），取其中属于本次请求的那个候选人。
	if known_ids:
		for side in ("from", "to"):
			party = item.get(side)
			uid = _as_int(party.get("uid")) if isinstance(party, dict) else None
			if uid is not None and uid in known_ids:
				return uid
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
	for key in ("lastMessageList", "lastMsgList", "messages", "messageList", "msgList", "result", "friendList", "list"):
		value = data.get(key)
		if isinstance(value, list):
			return _list_dict_items(value)
	# 也兼容 {friendId: 消息} 的映射形状。
	items: list[dict[str, Any]] = []
	for key, value in data.items():
		friend_id = _as_int(key)
		if friend_id is None:
			continue
		if isinstance(value, dict):
			items.append({"friendId": friend_id, **value})
		elif isinstance(value, str):
			items.append({"friendId": friend_id, "lastMsg": value})
	return items


def _last_info(item: dict[str, Any]) -> dict[str, Any]:
	for key in ("lastMsgInfo", "lastMessageInfo"):
		info = item.get(key)
		if isinstance(info, dict):
			return info
	return {}


def _message_status_label(item: dict[str, Any]) -> str | None:
	status = item.get("msg_status") if item.get("msg_status") is not None else item.get("status")
	info = _last_info(item)
	if info.get("status") not in (None, ""):
		status = info.get("status")
	if isinstance(status, str):
		return status or None
	if isinstance(status, bool) or not isinstance(status, int):
		return None
	return _RECRUITER_MSG_STATUS_LABELS.get(status)


def _message_text(item: dict[str, Any]) -> str | None:
	for key in ("last_msg", "lastMsg", "lastText", "content", "text", "message", "msgContent"):
		value = item.get(key)
		if isinstance(value, str | int | float) and not isinstance(value, bool) and value != "":
			return str(value)
	body = item.get("body")
	if isinstance(body, dict) and body.get("text") not in (None, ""):
		return str(body["text"])
	info = _last_info(item)
	for key in ("showText", "text"):
		if info.get(key) not in (None, ""):
			return str(info[key])
	return None


def _unread_count(item: dict[str, Any]) -> int | None:
	for key in ("unread", "unreadMsgCount", "unreadCount", "newMsgCount"):
		value = item.get(key)
		if isinstance(value, bool) or value in (None, ""):
			continue
		try:
			count = int(str(value))
		except ValueError:
			continue
		if count >= 0:
			return count
	return None


def _message_time(item: dict[str, Any]) -> str | None:
	for key in ("last_time", "lastTime", "lastTS", "lastMsgTime", "time", "timestamp"):
		formatted = _format_chat_time(item.get(key))
		if formatted is not None:
			return formatted
	return _format_chat_time(_last_info(item).get("msgTime"))


def _normalize_last_message(item: dict[str, Any], known_ids: set[int] | None = None) -> dict[str, Any]:
	return {
		"friendId": _friend_id_for(item, known_ids),
		"unread": _unread_count(item),
		"msg_status": _message_status_label(item),
		"last_msg": _message_text(item),
		"last_time": _message_time(item),
	}


def _has_summary(message: dict[str, Any]) -> bool:
	return any(message.get(key) is not None for key in ("unread", "msg_status", "last_msg", "last_time"))


def _summaries_by_friend(items: list[dict[str, Any]], known_ids: set[int] | None = None) -> dict[int, dict[str, Any]]:
	summaries: dict[int, dict[str, Any]] = {}
	for item in items:
		message = _normalize_last_message(item, known_ids)
		friend_id = message["friendId"]
		if friend_id is not None and _has_summary(message):
			summaries.setdefault(friend_id, message)
	return summaries


def _merge_last_messages(
	friend_items: list[dict[str, Any]],
	message_items: list[dict[str, Any]],
	*,
	source: str = "last_messages",
	page_summaries: dict[int, dict[str, Any]] | None = None,
) -> dict[str, int]:
	"""把摘要合并进沟通列表条目，返回各来源命中的条数。

	来源优先级：聊天页已加载的会话列表（page） > 最近消息接口（last_messages）
	> 沟通列表条目自身字段（friend_list，只有 updateTime 时 last_time 取它）。
	拿不到的字段一律给 null，不再填「-」「未知」这类占位。
	"""
	known_ids = {fid for item in friend_items if (fid := _friend_id_for(item)) is not None}
	api_summaries = _summaries_by_friend(message_items, known_ids)
	counts: dict[str, int] = {}
	for item in friend_items:
		friend_id = _friend_id_for(item)
		own = _normalize_last_message(item)
		if friend_id is not None and page_summaries and friend_id in page_summaries:
			message, used = page_summaries[friend_id], "page"
		elif friend_id is not None and friend_id in api_summaries:
			message, used = api_summaries[friend_id], source
		else:
			message, used = own, "friend_list"
		item.update({key: value for key, value in message.items() if key != "friendId"})
		if own["unread"] is not None:
			item["unread"] = own["unread"]
		if item.get("last_time") is None:
			update_time = _format_chat_time(item.get("updateTime"))
			if update_time is not None:
				item["last_time"] = update_time
				item["last_time_source"] = "updateTime"
		item["summary_source"] = used
		counts[used] = counts.get(used, 0) + 1
	return counts


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


def _page_summaries(platform: Any) -> dict[int, dict[str, Any]]:
	"""CDP 模式下读聊天页已加载的会话列表（只读页面内存，不发请求）；拿不到返回空。"""
	snapshot_fn = getattr(platform, "chat_list_snapshot", None)
	if not callable(snapshot_fn):
		return {}
	try:
		snapshot = snapshot_fn()
	except Exception:  # noqa: BLE001 — 页面快照只是增强，失败退回接口/列表字段
		return {}
	if not isinstance(snapshot, dict) or not snapshot.get("ok"):
		return {}
	return _summaries_by_friend(_list_dict_items(snapshot.get("items")))


def _enrich_chat_summaries(platform: Any, friend_items: list[dict[str, Any]]) -> dict[str, Any]:
	"""给沟通列表补未读数 / 最后一条消息 / 时间，返回来源统计（放进 hints）。

	请求量：页面快照不发请求；只有页面里没覆盖到的会话才调一次 userLastMsg（单次、串行）。
	"""
	friend_ids = _friend_ids_from_items(friend_items)
	if not friend_ids:
		return {}
	page = _page_summaries(platform)
	missing = [fid for fid in friend_ids if fid not in page]
	message_items: list[dict[str, Any]] = []
	hint: dict[str, Any] = {}
	if missing:
		try:
			last_messages = platform.last_messages(missing)
		except NotImplementedError:
			last_messages = None
		if isinstance(last_messages, dict) and platform.is_success(last_messages):
			message_items = _message_items(platform.unwrap_data(last_messages) or {})
		elif isinstance(last_messages, dict):
			_code, message = platform.parse_error(last_messages)
			hint["last_messages_error"] = message or f"code={last_messages.get('code')}"
	counts = _merge_last_messages(friend_items, message_items, page_summaries=page)
	hint.update({"counts": counts})
	return hint


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
			handle_platform_error_output(ctx, "recruiter-chat", platform, result, fallback_message="沟通列表获取失败")
			return
		data = _friend_data(platform.unwrap_data(result))
		friend_items = _friend_items(data)
		summary_hint = _enrich_chat_summaries(platform, friend_items)
		hints: dict[str, Any] = {"next_actions": [
			"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历",
			"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
		]}
		if summary_hint:
			hints["summary_sources"] = summary_hint
		handle_output(ctx, "recruiter-chat", data, hints=hints)


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
			handle_platform_error_output(ctx, "recruiter-chatmsg", platform, result, fallback_message="聊天记录获取失败")
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
				handle_platform_error_output(ctx, "recruiter-last-messages", platform, error, fallback_message="沟通列表获取失败")
				return
		if not ids:
			handle_output(ctx, "recruiter-last-messages", {"friend_ids": [], "messages": []})
			return

		result = platform.last_messages(ids)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-last-messages", platform, result, fallback_message="最近消息获取失败")
			return
		data = platform.unwrap_data(result) or {}
		known = set(ids)
		messages = [_normalize_last_message(item, known) for item in _message_items(data)]
		handle_output(
			ctx, "recruiter-last-messages",
			{"friend_ids": ids, "messages": messages},
			hints={"next_actions": [
				"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
				"boss hr reply <friend_id> <message> — 回复候选人消息",
			]},
		)
