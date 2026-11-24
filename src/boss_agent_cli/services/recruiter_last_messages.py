"""招聘者「最近消息」批量查询：CLI `hr last-messages` 与向导 goal 共用。

userLastMsg 批量：前端自己每页最多 100 个；一次塞 300+ 个会被拒（「未知的非法参数」）。
CDP 模式下请求串行，这里再保守一点：每批 50 个，默认只补前 100 个会话（最多 2 次请求）。
批量切分由调用方负责——平台接口单批最多 100 个 friendId，因此统一走本模块按批串行。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.api.client import RISK_ERROR_BY_CODE

DEFAULT_SUMMARY_LIMIT = 100
MAX_SUMMARY_LIMIT = 300
LAST_MESSAGES_BATCH_SIZE = 50


def as_friend_int(value: Any) -> int | None:
	"""friendId 口径的宽松整数；bool / 非整数文本返回 None。"""
	if value in (None, "") or isinstance(value, bool):
		return None
	try:
		return int(str(value))
	except (TypeError, ValueError):
		return None


def friend_id_for(item: dict[str, Any], known_ids: set[int] | None = None) -> int | None:
	for key in ("friendId", "friend_id", "uid", "gid"):
		value = as_friend_int(item.get(key))
		if value is not None:
			return value
	# userLastMsg 可能直接返回消息对象（from/to 两端 uid），取其中属于本次请求的那个候选人。
	if known_ids:
		for side in ("from", "to"):
			party = item.get(side)
			uid = as_friend_int(party.get("uid")) if isinstance(party, dict) else None
			if uid is not None and uid in known_ids:
				return uid
	return None


def list_dict_items(value: Any) -> list[dict[str, Any]]:
	if isinstance(value, list):
		return [item for item in value if isinstance(item, dict)]
	return []


def message_items(data: Any) -> list[dict[str, Any]]:
	if isinstance(data, list):
		return list_dict_items(data)
	if not isinstance(data, dict):
		return []
	for key in ("lastMessageList", "lastMsgList", "messages", "messageList", "msgList", "result", "friendList", "list", "items"):
		value = data.get(key)
		if isinstance(value, list):
			return list_dict_items(value)
	# 也兼容 {friendId: 消息} 的映射形状。
	items: list[dict[str, Any]] = []
	for key, value in data.items():
		friend_id = as_friend_int(key)
		if friend_id is None:
			continue
		if isinstance(value, dict):
			items.append({"friendId": friend_id, **value})
		elif isinstance(value, str):
			items.append({"friendId": friend_id, "lastMsg": value})
	return items


def friend_sources(friend_items: list[dict[str, Any]]) -> dict[int, int]:
	sources: dict[int, int] = {}
	for item in friend_items:
		fid = friend_id_for(item)
		src = as_friend_int(item.get("friendSource"))
		if fid is not None and src is not None:
			sources[fid] = src
	return sources


def fetch_last_messages(
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
				items.extend(message_items(platform.unwrap_data(result) or {}))
				continue
			stats["failed_response"] = result
			if isinstance(result, dict):
				code, message = platform.parse_error(result)
				risk_cls = RISK_ERROR_BY_CODE.get(code)
				if risk_cls is not None:
					# 响应字典形态的风控同样是停止信号：不能降级成 partial_error 后继续输出 ok:true。
					raise risk_cls(message or code)
				stats["error"] = message or f"code={result.get('code')}"
				if code is not None:
					stats["error_code"] = code
			else:
				stats["error"] = "最近消息接口无响应"
			return items, stats
	return items, stats
