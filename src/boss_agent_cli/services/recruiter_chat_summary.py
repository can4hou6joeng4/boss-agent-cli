"""招聘者沟通列表摘要：把最近消息接口结果合并进 friend_list 条目。

CLI `hr chat` 与向导 goal `communication` 共用同一份归一化 / 合并实现，
未读数、最后一条消息、时间与来源统计口径只有这一处真源。
"""

from __future__ import annotations

import math
from typing import Any

from boss_agent_cli.services.chat_utils import MSG_STATUS_LABELS, message_text_value, safe_local_time
from boss_agent_cli.services.pagination import dict_rows_from
from boss_agent_cli.services.recruiter_last_messages import (
	DEFAULT_SUMMARY_LIMIT,
	MAX_SUMMARY_LIMIT,
	fetch_last_messages,
	friend_id_for,
	friend_sources,
)

RECRUITER_MSG_STATUS_LABELS = {0: "发送中", **MSG_STATUS_LABELS}

# 未读总数只读聊天页内存（uncountTab$ 第 1 项），不额外发请求；页面不可用时为 null。
UNREAD_TOTAL_TAB = "1"
UNREAD_TOTAL_SOURCE = "page.uncountTab$[1]"


def format_chat_time(value: Any) -> str | None:
	"""毫秒时间戳格式化为本地 MM-DD HH:MM；平台给的字符串原样返回；没有就返回 None。"""
	if value in (None, "") or isinstance(value, bool):
		return None
	if isinstance(value, int | float):
		if value <= 0:
			return None
		# 越界 / 非有限时间戳是确定性坏数据，按「无时间」处理（Issue #1295）。
		dt = safe_local_time(value)
		return dt.strftime("%m-%d %H:%M") if dt is not None else None
	if not isinstance(value, str):
		# dict / list 等类型漂移不能变成 "{'a': 1}" 写进输出数据（Issue #1842）。
		return None
	text = value.strip()
	if text.isdigit():
		try:
			parsed = int(text)
		except (ValueError, OverflowError):
			# 超过 4300 位的数字串 ``int()`` 抛 ValueError：原样展示平台字符串。
			return text or None
		return format_chat_time(parsed)
	return text or None


def friend_items(data: Any) -> list[dict[str, Any]]:
	"""沟通列表的实际渲染行：与 ``pagination.total`` 共用同一份提取口径。"""
	return dict_rows_from(data, "friendList", "result", "list") or []


def friend_ids_from_items(items: list[dict[str, Any]]) -> list[int]:
	ids: list[int] = []
	for item in items:
		friend_id = friend_id_for(item)
		if friend_id is not None and friend_id not in ids:
			ids.append(friend_id)
	return ids


def last_info(item: dict[str, Any]) -> dict[str, Any]:
	for key in ("lastMsgInfo", "lastMessageInfo"):
		info = item.get(key)
		if isinstance(info, dict):
			return info
	return {}


def last_is_self(item: dict[str, Any], friend_id: int | None) -> bool | None:
	"""最后一条是不是招聘者自己发的；前端同样用 lastMsgInfo.fromId !== friendId 判断。"""
	flag = item.get("lastIsSelf")
	if isinstance(flag, bool):
		return flag
	from_id = _as_int(last_info(item).get("fromId"))
	if from_id is None:
		# 最近消息也兼容 from/to 消息对象；候选人来信没有发送状态（Issue #3003）。
		sender = item.get("from")
		from_id = _as_int(sender.get("uid")) if isinstance(sender, dict) else None
	if from_id is None or friend_id is None:
		return None
	return from_id != friend_id


def _as_int(value: Any) -> int | None:
	if value in (None, "") or isinstance(value, bool):
		return None
	try:
		return int(str(value))
	except (TypeError, ValueError):
		return None


def message_status_label(item: dict[str, Any], friend_id: int | None = None) -> str | None:
	# 已读/送达只对招聘者自己发出的消息有意义；候选人发来的消息 status 恒为 0，不能当「发送中」。
	if last_is_self(item, friend_id) is False:
		return None
	status = item.get("msg_status") if item.get("msg_status") is not None else item.get("status")
	if item.get("lastMsgStatus") not in (None, ""):
		status = item.get("lastMsgStatus")
	info = last_info(item)
	if info.get("status") not in (None, ""):
		status = info.get("status")
	if isinstance(status, str):
		return status or None
	if isinstance(status, bool) or not isinstance(status, int):
		return None
	return RECRUITER_MSG_STATUS_LABELS.get(status)


def message_text(item: dict[str, Any], *, skip_blank: bool = False) -> str | None:
	# 值归一在 chat_utils.message_text_value：布尔 / 容器 / 非有限数值按缺失
	# 处理（Issue #2730），候选人 chatmsg 共用同一份（Issue #2749）。
	body = item.get("body")
	fields = (
		(item, ("last_msg", "lastMsg", "lastText", "content", "text", "message", "msgContent")),
		(body if isinstance(body, dict) else {}, ("text", "content")),
		(last_info(item), ("showText", "text")),
	)
	for source, keys in fields:
		for key in keys:
			text = message_text_value(source.get(key))
			if text is not None and (not skip_blank or text.strip()):
				return text
	return None


def unread_count(item: dict[str, Any]) -> int | None:
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


def message_time(item: dict[str, Any]) -> str | None:
	# 先用毫秒时间戳（lastTS / lastMsgInfo.msgTime），userLastMsg 的 lastTime 只是「12:00」这种展示串。
	for value in (
		item.get("last_time"),
		item.get("lastTS"),
		item.get("lastMsgTime"),
		last_info(item).get("msgTime"),
		item.get("lastTime"),
		item.get("time"),
		item.get("timestamp"),
	):
		formatted = format_chat_time(value)
		if formatted is not None:
			return formatted
	return None


def normalize_last_message(item: dict[str, Any], known_ids: set[int] | None = None) -> dict[str, Any]:
	friend_id = friend_id_for(item, known_ids)
	return {
		"friendId": friend_id,
		"unread": unread_count(item),
		"msg_status": message_status_label(item, friend_id),
		"last_msg": message_text(item),
		"last_time": message_time(item),
	}


def has_summary(message: dict[str, Any]) -> bool:
	return any(message.get(key) is not None for key in ("unread", "msg_status", "last_msg", "last_time"))


def summaries_by_friend(
	items: list[dict[str, Any]], known_ids: set[int] | None = None
) -> dict[int, dict[str, Any]]:
	summaries: dict[int, dict[str, Any]] = {}
	for item in items:
		message = normalize_last_message(item, known_ids)
		friend_id = message["friendId"]
		if friend_id is not None and has_summary(message):
			summaries.setdefault(friend_id, message)
	return summaries


def merge_last_messages(
	friend_rows: list[dict[str, Any]],
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
	known_ids = {fid for item in friend_rows if (fid := friend_id_for(item)) is not None}
	api_summaries = summaries_by_friend(message_items, known_ids)
	counts: dict[str, int] = {}
	for item in friend_rows:
		friend_id = friend_id_for(item)
		own = normalize_last_message(item)
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
			update_time = format_chat_time(item.get("updateTime"))
			if update_time is not None:
				item["last_time"] = update_time
				item["last_time_source"] = "updateTime"
		item["summary_source"] = used
		counts[used] = counts.get(used, 0) + 1
	return counts


def clean_unread_by_tab(value: Any) -> dict[str, int] | None:
	"""页面 uncountTab$ 原始计数：只留非负整数，键统一成字符串。"""
	if not isinstance(value, dict):
		return None
	cleaned: dict[str, int] = {}
	for key, count in value.items():
		if isinstance(count, bool) or not isinstance(count, int | float):
			continue
		# NaN / Infinity 让 int(count) 抛 ValueError / OverflowError，整条
		# hr chat 会被兜成可重试的网络错误（Issue #1837）。int 不做
		# isfinite（巨大的合法整数转 float 会 OverflowError）。
		if isinstance(count, float) and not math.isfinite(count):
			continue
		if count < 0 or count != int(count):
			continue
		cleaned[str(key)] = int(count)
	return cleaned


def unread_totals(page_meta: dict[str, Any] | None) -> dict[str, Any]:
	"""聊天页显示的未读总数（uncountTab$ 第 1 项，实测与页面 UI 一致）；页面不可用时为 null。"""
	by_tab = (page_meta or {}).get("unread_by_tab")
	total = by_tab.get(UNREAD_TOTAL_TAB) if isinstance(by_tab, dict) else None
	return {
		"total_unread": total,
		"unread_by_tab": by_tab if isinstance(by_tab, dict) else None,
		"total_unread_source": UNREAD_TOTAL_SOURCE if total is not None else None,
	}


def enrich_friend_summaries(
	platform: Any,
	friend_rows: list[dict[str, Any]],
	*,
	summary_limit: int = DEFAULT_SUMMARY_LIMIT,
	page_summaries: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
	"""给沟通列表补未读数 / 最后一条消息 / 时间，返回来源统计（CLI hints.summary_sources 同词汇）。

	`page_summaries` 是聊天页已加载的会话摘要：覆盖到的会话不再发请求（仅 CDP 模式有）。
	请求量：只取未覆盖会话的前 summary_limit 个，按 50 个一批串行调 userLastMsg。
	"""
	friend_ids = friend_ids_from_items(friend_rows)
	covered = page_summaries or {}
	missing = [fid for fid in friend_ids if fid not in covered]
	limit = max(0, min(summary_limit, MAX_SUMMARY_LIMIT))
	wanted = missing[:limit]
	msg_items: list[dict[str, Any]] = []
	stats: dict[str, Any] = {"requests": 0}
	hint: dict[str, Any] = {}
	if wanted:
		msg_items, stats = fetch_last_messages(platform, wanted, sources=friend_sources(friend_rows))
		hint["last_messages_requests"] = stats["requests"]
		if "error" in stats:
			hint["last_messages_error"] = stats["error"]
			if "error_code" in stats:
				hint["last_messages_error_code"] = stats["error_code"]
	if len(missing) > len(wanted):
		hint["last_messages_skipped"] = len(missing) - len(wanted)
		hint["summary_limit"] = limit
	hint.update({"counts": merge_last_messages(friend_rows, msg_items, page_summaries=page_summaries)})
	return hint
