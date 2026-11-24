import time
from typing import Any

from boss_agent_cli.api.models import dict_items, field_text
from boss_agent_cli.services.chat_utils import RELATION_LABELS, int_code, safe_local_time


_FOLLOW_UP_STATES = {"reply_needed", "follow_up", "interview"}


def _ts_to_label(ts_ms: int) -> str:
	if not ts_ms:
		return "-"
	# 越界 / 非有限时间戳按「无时间」输出（Issue #1295）。
	dt = safe_local_time(ts_ms)
	return dt.strftime("%m-%d %H:%M") if dt is not None else "-"


def now_ts_or_clock(now_ts_ms: int | None) -> int:
	"""``--now-ts-ms 0`` 是合法的显式值（测试钩子），不能用真值回退（Issue #1298）。"""
	return now_ts_ms if now_ts_ms is not None else int(time.time() * 1000)


def _chat_stage(item: dict[str, Any], *, now_ts_ms: int, stale_days: int) -> str:
	# 平台数值编码只认整数：JSON 布尔不能让会话变成 reply_needed（Issue #915）。
	unread = int_code(item.get("unreadMsgCount")) or 0
	relation_type = int_code(item.get("relationType"))
	last_ts = int_code(item.get("lastTS")) or 0
	if unread > 0:
		return "reply_needed"
	if relation_type == 3:
		return "applied"
	if last_ts and now_ts_ms - last_ts >= stale_days * 24 * 3600 * 1000:
		return "follow_up"
	return "chatting"


def build_pipeline_items(*, chat_items: list[dict[str, Any]], interview_items: list[dict[str, Any]], now_ts_ms: int, stale_days: int = 3) -> list[dict[str, Any]]:
	items: list[dict[str, Any]] = []

	# 沟通 / 面试列表里的元素级漂移在这里统一跳过：本函数是 pipeline / digest /
	# wizard 共用的聚合点，坏元素只丢自己，不拖垮整份日报（Issue #1141 同一口径）。
	for raw in dict_items(chat_items):
		unread = int_code(raw.get("unreadMsgCount")) or 0
		stage = _chat_stage(raw, now_ts_ms=now_ts_ms, stale_days=stale_days)
		items.append(
			{
				"source": "chat",
				"stage": stage,
				# 文本字段统一走 field_text：`raw.get(k) or 默认值` 会让 nan / inf
				# 穿过（真值）并把容器 str() 成 Python repr 写进产物 / Markdown
				# （Issue #2739）。
				"security_id": field_text(raw, "securityId"),
				"job_id": field_text(raw, "encryptJobId"),
				"company": field_text(raw, "brandName", "-"),
				"title": field_text(raw, "title", "-"),
				"relation": RELATION_LABELS.get(int_code(raw.get("relationType")), "未知"),
				"unread": unread,
				"last_msg": field_text(raw, "lastMsg", "-"),
				"last_time": _ts_to_label(int_code(raw.get("lastTS")) or 0),
				"reason": "存在未读消息" if unread > 0 else "需要继续推进" if stage == "follow_up" else "会话进行中",
			}
		)

	for raw in dict_items(interview_items):
		items.append(
			{
				"source": "interview",
				"stage": "interview",
				"security_id": field_text(raw, "securityId"),
				"job_id": field_text(raw, "encryptJobId"),
				"company": field_text(raw, "brandName", "-"),
				"title": field_text(raw, "jobName", "-"),
				"relation": "面试",
				"unread": 0,
				"last_msg": field_text(raw, "statusDesc", "-"),
				"last_time": field_text(raw, "interviewTime", "-"),
				"reason": "存在待处理面试安排",
			}
		)

	priority = {"reply_needed": 0, "interview": 1, "follow_up": 2, "applied": 3, "chatting": 4}
	# 排序键统一转字符串：平台字段类型漂移（数字 / 对象）时，跨类型比较会抛
	# TypeError 并让整份日报落成 INTERNAL_ERROR（Issue #1297）。
	return sorted(
		items,
		key=lambda item: (priority.get(item["stage"], 99), str(item["company"]), str(item["title"])),
	)


def select_follow_up_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [item for item in items if item.get("stage") in _FOLLOW_UP_STATES]
