import datetime

from boss_agent_cli.commands.chat_utils import RELATION_LABELS


_FOLLOW_UP_STATES = {"reply_needed", "follow_up", "interview"}


def _ts_to_label(ts_ms: int) -> str:
	if not ts_ms:
		return "-"
	dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
	return dt.strftime("%m-%d %H:%M")


def _chat_stage(item: dict, *, now_ts_ms: int, stale_days: int) -> str:
	unread = item.get("unreadMsgCount") or 0
	relation_type = item.get("relationType")
	last_ts = item.get("lastTS") or 0
	if unread > 0:
		return "reply_needed"
	if relation_type == 3:
		return "applied"
	if last_ts and now_ts_ms - last_ts >= stale_days * 24 * 3600 * 1000:
		return "follow_up"
	return "chatting"


def build_pipeline_items(*, chat_items: list[dict], interview_items: list[dict], now_ts_ms: int, stale_days: int = 3) -> list[dict]:
	items = []

	for raw in chat_items:
		items.append(
			{
				"source": "chat",
				"stage": _chat_stage(raw, now_ts_ms=now_ts_ms, stale_days=stale_days),
				"security_id": raw.get("securityId") or "",
				"job_id": raw.get("encryptJobId") or "",
				"company": raw.get("brandName") or "-",
				"title": raw.get("title") or "-",
				"relation": RELATION_LABELS.get(raw.get("relationType"), "未知"),
				"unread": raw.get("unreadMsgCount") or 0,
				"last_msg": raw.get("lastMsg") or "-",
				"last_time": _ts_to_label(raw.get("lastTS") or 0),
				"reason": "存在未读消息" if (raw.get("unreadMsgCount") or 0) > 0 else "需要继续推进" if _chat_stage(raw, now_ts_ms=now_ts_ms, stale_days=stale_days) == "follow_up" else "会话进行中",
			}
		)

	for raw in interview_items:
		items.append(
			{
				"source": "interview",
				"stage": "interview",
				"security_id": raw.get("securityId") or "",
				"job_id": raw.get("encryptJobId") or "",
				"company": raw.get("brandName") or "-",
				"title": raw.get("jobName") or "-",
				"relation": "面试",
				"unread": 0,
				"last_msg": raw.get("statusDesc") or "-",
				"last_time": raw.get("interviewTime") or "-",
				"reason": "存在待处理面试安排",
			}
		)

	priority = {"reply_needed": 0, "interview": 1, "follow_up": 2, "applied": 3, "chatting": 4}
	return sorted(items, key=lambda item: (priority.get(item["stage"], 99), item["company"], item["title"]))


def select_follow_up_candidates(items: list[dict]) -> list[dict]:
	return [item for item in items if item.get("stage") in _FOLLOW_UP_STATES]
