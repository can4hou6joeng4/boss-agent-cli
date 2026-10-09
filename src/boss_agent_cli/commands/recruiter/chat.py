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

# userLastMsg 批量：前端自己每页最多 100 个；一次塞 300+ 个会被拒（「未知的非法参数」）。
# CDP 模式下请求串行，这里再保守一点：每批 50 个，默认只补前 100 个会话（最多 2 次请求）。
LAST_MESSAGES_BATCH_SIZE = 50
DEFAULT_SUMMARY_LIMIT = 100
MAX_SUMMARY_LIMIT = 300


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


def _last_is_self(item: dict[str, Any], friend_id: int | None) -> bool | None:
	"""最后一条是不是招聘者自己发的；前端同样用 lastMsgInfo.fromId !== friendId 判断。"""
	flag = item.get("lastIsSelf")
	if isinstance(flag, bool):
		return flag
	from_id = _as_int(_last_info(item).get("fromId"))
	if from_id is None or friend_id is None:
		return None
	return from_id != friend_id


def _message_status_label(item: dict[str, Any], friend_id: int | None = None) -> str | None:
	# 已读/送达只对招聘者自己发出的消息有意义；候选人发来的消息 status 恒为 0，不能当「发送中」。
	if _last_is_self(item, friend_id) is False:
		return None
	status = item.get("msg_status") if item.get("msg_status") is not None else item.get("status")
	if item.get("lastMsgStatus") not in (None, ""):
		status = item.get("lastMsgStatus")
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
	# 先用毫秒时间戳（lastTS / lastMsgInfo.msgTime），userLastMsg 的 lastTime 只是「12:00」这种展示串。
	for value in (item.get("last_time"), item.get("lastTS"), item.get("lastMsgTime"), _last_info(item).get("msgTime"),
			item.get("lastTime"), item.get("time"), item.get("timestamp")):
		formatted = _format_chat_time(value)
		if formatted is not None:
			return formatted
	return None


def _normalize_last_message(item: dict[str, Any], known_ids: set[int] | None = None) -> dict[str, Any]:
	friend_id = _friend_id_for(item, known_ids)
	return {
		"friendId": friend_id,
		"unread": _unread_count(item),
		"msg_status": _message_status_label(item, friend_id),
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


def _fetch_friend_ids(
	platform: Any, *, page: int, label_id: int, job_id: str | None,
) -> tuple[list[int], dict[int, int], dict[str, Any] | None]:
	result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
	if not platform.is_success(result):
		return [], {}, result
	items = _friend_items(_friend_data(platform.unwrap_data(result)))
	return _friend_ids_from_items(items), _friend_sources(items), None


UNREAD_TOTAL_TAB = "1"
UNREAD_TOTAL_SOURCE = "page.uncountTab$[1]"


def _clean_unread_by_tab(value: Any) -> dict[str, int] | None:
	"""页面 uncountTab$ 原始计数：只留非负整数，键统一成字符串。"""
	if not isinstance(value, dict):
		return None
	cleaned: dict[str, int] = {}
	for key, count in value.items():
		if isinstance(count, bool) or not isinstance(count, int | float):
			continue
		if count < 0 or count != int(count):
			continue
		cleaned[str(key)] = int(count)
	return cleaned


def _unread_totals(page_meta: dict[str, Any] | None) -> dict[str, Any]:
	"""聊天页显示的未读总数（uncountTab$ 第 1 项，实测与页面 UI 一致）；页面不可用时为 null。"""
	by_tab = (page_meta or {}).get("unread_by_tab")
	total = by_tab.get(UNREAD_TOTAL_TAB) if isinstance(by_tab, dict) else None
	return {
		"total_unread": total,
		"unread_by_tab": by_tab if isinstance(by_tab, dict) else None,
		"total_unread_source": UNREAD_TOTAL_SOURCE if total is not None else None,
	}


def _page_summaries(platform: Any) -> tuple[dict[int, dict[str, Any]], dict[str, Any] | None]:
	"""CDP 模式下读聊天页已加载的会话列表（只读页面内存，不发请求）；拿不到返回空。

	返回 (按 friendId 的摘要, 快照元信息)；元信息含数据来源路径、已加载条数、页面是否还有更多。
	"""
	snapshot_fn = getattr(platform, "chat_list_snapshot", None)
	if not callable(snapshot_fn):
		return {}, None
	try:
		snapshot = snapshot_fn()
	except Exception:  # noqa: BLE001 — 页面快照只是增强，失败退回接口/列表字段
		return {}, None
	if not isinstance(snapshot, dict) or not snapshot.get("ok"):
		return {}, None
	summaries = _summaries_by_friend(_list_dict_items(snapshot.get("items")))
	meta: dict[str, Any] = {"loaded": len(summaries)}
	unread_by_tab = _clean_unread_by_tab(snapshot.get("unread_by_tab"))
	if unread_by_tab is not None:
		meta["unread_by_tab"] = unread_by_tab
	if isinstance(snapshot.get("source"), str):
		meta["source"] = snapshot["source"]
	if isinstance(snapshot.get("has_more"), bool):
		meta["has_more"] = snapshot["has_more"]
	return summaries, meta


def _fetch_last_messages(
	platform: Any,
	friend_ids: list[int],
	*,
	sources: dict[int, int] | None = None,
	batch_size: int = LAST_MESSAGES_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
	"""按 friendSource 分组、按批串行调 userLastMsg；任一批失败立即停止，不再继续发请求。

	返回 (消息条目, 统计)；统计里带 requests，失败时另带 error/error_code/failed_response。
	"""
	groups: dict[int, list[int]] = {}
	for fid in friend_ids:
		src = 1 if (sources or {}).get(fid) == 1 else 0
		groups.setdefault(src, []).append(fid)
	items: list[dict[str, Any]] = []
	stats: dict[str, Any] = {"requests": 0}
	for src in sorted(groups):
		ids = groups[src]
		for start in range(0, len(ids), batch_size):
			batch = ids[start:start + batch_size]
			try:
				result = platform.last_messages(batch, src=src) if src else platform.last_messages(batch)
			except NotImplementedError:
				return items, stats
			stats["requests"] += 1
			if isinstance(result, dict) and platform.is_success(result):
				items.extend(_message_items(platform.unwrap_data(result) or {}))
				continue
			stats["failed_response"] = result
			if isinstance(result, dict):
				code, message = platform.parse_error(result)
				stats["error"] = message or f"code={result.get('code')}"
				if code is not None:
					stats["error_code"] = code
			else:
				stats["error"] = "最近消息接口无响应"
			return items, stats
	return items, stats


def _friend_sources(friend_items: list[dict[str, Any]]) -> dict[int, int]:
	sources: dict[int, int] = {}
	for item in friend_items:
		fid = _friend_id_for(item)
		src = _as_int(item.get("friendSource"))
		if fid is not None and src is not None:
			sources[fid] = src
	return sources


def _enrich_chat_summaries(
	platform: Any,
	friend_items: list[dict[str, Any]],
	*,
	summary_limit: int = DEFAULT_SUMMARY_LIMIT,
) -> dict[str, Any]:
	"""给沟通列表补未读数 / 最后一条消息 / 时间，返回来源统计（放进 hints）。

	请求量：页面快照不发请求；页面没覆盖到的会话里只取列表前 summary_limit 个，
	按 50 个一批串行调 userLastMsg（默认最多 2 次请求），其余会话只保留列表自带字段。
	"""
	friend_ids = _friend_ids_from_items(friend_items)
	page, page_meta = _page_summaries(platform)
	if not friend_ids:
		return {"page_snapshot": page_meta} if page_meta is not None else {}
	missing = [fid for fid in friend_ids if fid not in page]
	limit = max(0, min(summary_limit, MAX_SUMMARY_LIMIT))
	wanted = missing[:limit]
	message_items: list[dict[str, Any]] = []
	hint: dict[str, Any] = {}
	if page_meta is not None:
		hint["page_snapshot"] = page_meta
	if wanted:
		message_items, stats = _fetch_last_messages(platform, wanted, sources=_friend_sources(friend_items))
		hint["last_messages_requests"] = stats["requests"]
		if "error" in stats:
			hint["last_messages_error"] = stats["error"]
			if "error_code" in stats:
				hint["last_messages_error_code"] = stats["error_code"]
	if len(missing) > len(wanted):
		hint["last_messages_skipped"] = len(missing) - len(wanted)
		hint["summary_limit"] = limit
	counts = _merge_last_messages(friend_items, message_items, page_summaries=page)
	hint.update({"counts": counts})
	return hint


@click.command("chat")
@click.option("--page", default=1, type=int, help="页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=int, help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.option(
	"--summary-limit", default=DEFAULT_SUMMARY_LIMIT, show_default=True,
	type=click.IntRange(0, MAX_SUMMARY_LIMIT),
	help="最多给前多少个会话补最近消息（每 50 个一次串行请求；0=不请求）",
)
@click.pass_context
@handle_auth_errors("recruiter-chat")
def recruiter_chat_cmd(ctx: click.Context, page: int, job_id: str | None, label_id: int, summary_limit: int) -> None:
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
		summary_hint = _enrich_chat_summaries(platform, friend_items, summary_limit=summary_limit)
		hints: dict[str, Any] = {"next_actions": [
			"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历",
			"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
		]}
		# 未读总数只读页面内存（uncountTab$），不额外发请求；放在 hints 顶层，拿不到为 null。
		hints.update(_unread_totals(summary_hint.get("page_snapshot")))
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
@click.option(
	"--limit", default=DEFAULT_SUMMARY_LIMIT, show_default=True,
	type=click.IntRange(1, MAX_SUMMARY_LIMIT),
	help="最多查询多少个会话（每 50 个一次串行请求）",
)
@click.pass_context
@handle_auth_errors("recruiter-last-messages")
def recruiter_last_messages_cmd(
	ctx: click.Context,
	page: int,
	job_id: str | None,
	label_id: int,
	friend_ids: tuple[int, ...],
	limit: int,
) -> None:
	"""批量查看候选人最近消息摘要"""
	if not require_compliance_allowed(ctx, "recruiter-last-messages"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		ids = list(dict.fromkeys(friend_ids))
		sources: dict[int, int] = {}
		if not ids:
			ids, sources, error = _fetch_friend_ids(platform, page=page, label_id=label_id, job_id=job_id)
			if error is not None:
				handle_platform_error_output(ctx, "recruiter-last-messages", platform, error, fallback_message="沟通列表获取失败")
				return
		if not ids:
			handle_output(ctx, "recruiter-last-messages", {"friend_ids": [], "messages": []})
			return

		skipped = max(0, len(ids) - limit)
		ids = ids[:limit]
		items, stats = _fetch_last_messages(platform, ids, sources=sources)
		if "error" in stats and not items:
			handle_platform_error_output(
				ctx, "recruiter-last-messages", platform,
				stats["failed_response"],
				fallback_message="最近消息获取失败",
			)
			return
		known = set(ids)
		messages = [_normalize_last_message(item, known) for item in items]
		output: dict[str, Any] = {"friend_ids": ids, "messages": messages, "requests": stats["requests"]}
		if skipped:
			output["skipped"] = skipped
		if "error" in stats:
			output["partial_error"] = stats["error"]
		handle_output(
			ctx, "recruiter-last-messages",
			output,
			hints={"next_actions": [
				"boss hr chatmsg <friend_id> — 查看候选人沟通上下文",
				"boss hr reply <friend_id> <message> — 回复候选人消息",
			]},
		)
