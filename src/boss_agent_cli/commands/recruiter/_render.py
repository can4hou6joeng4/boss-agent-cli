"""招聘者侧命令的终端行映射与渲染（Issue #2774）。

平台响应字段会漂移：这里只做展示用的容错取值，缺失或类型不符时显示 ``-``，
不改变 JSON 信封里的原始数据。所有单元格最终由 ``render_simple_list`` 做
markup 转义与 ``None`` / 容器的归一。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.display import (
	render_list_result,
	render_message_panel,
	render_next_steps,
	render_sectioned_record,
)
from boss_agent_cli.services.pagination import rows_from_first
from boss_agent_cli.services.recruiter_chat_summary import (
	friend_items,
	format_chat_time,
	message_text,
	message_time,
	unread_count,
)


def _first_value(item: dict[str, Any], *keys: str) -> Any:
	"""按优先级取第一个非空值；空串与 None 视为未提供。"""
	for key in keys:
		value = item.get(key)
		if value is not None and value != "":
			return value
	return None


def _dict_rows(data: Any, *keys: str) -> list[dict[str, Any]]:
	"""从平台响应里取对象列表；字段缺失或类型漂移时返回空列表。

	与命令层的 ``pagination.total`` 共用 ``rows_from_first``，保证总数是本次
	实际渲染的行数（Issue #3034）。
	"""
	value = data if isinstance(data, list) else rows_from_first(data, *keys)
	if value is None:
		return []
	return [item for item in value if isinstance(item, dict)]


# ── 列表行映射 ────────────────────────────────────────────────────────


def friend_list_rows(data: Any) -> list[dict[str, Any]]:
	"""沟通 / 投递列表：姓名、公司、职位、未读数、最近消息与时间。"""
	rows: list[dict[str, Any]] = []
	for item in friend_items(data):
		rows.append(
			{
				"name": _first_value(item, "name", "friendName", "geekName"),
				"company": _first_value(item, "brandName", "company"),
				"job": _first_value(item, "jobName", "title"),
				"unread": unread_count(item),
				"time": message_time(item),
				"message": message_text(item),
			}
		)
	return rows


def candidate_list_rows(data: Any) -> list[dict[str, Any]]:
	"""候选人 / 推荐牛人列表：姓名、意向职位、城市、经验、学历、活跃度。"""
	rows: list[dict[str, Any]] = []
	for item in _dict_rows(data, "geekList", "items", "list", "result"):
		rows.append(
			{
				"name": _first_value(item, "name", "geekName"),
				"expect": _first_value(item, "expectPosition", "jobName", "title"),
				"city": _first_value(item, "city", "cityName"),
				"experience": _first_value(item, "workYearDesc", "experience"),
				"education": _first_value(item, "degreeCategory", "degreeName", "education"),
				"active": _first_value(item, "activeTimeDesc", "activeTime"),
			}
		)
	return rows


def job_list_rows(data: Any) -> list[dict[str, Any]]:
	"""职位管理列表：职位名、状态、城市、薪资与职位 ID。"""
	rows: list[dict[str, Any]] = []
	for item in _dict_rows(data, "jobList", "items", "list", "data"):
		rows.append(
			{
				"title": _first_value(item, "jobName", "title", "name"),
				"status": _first_value(item, "jobStatusDesc", "statusDesc", "status", "jobStatus"),
				"city": _first_value(item, "cityName", "city"),
				"salary": _first_value(item, "salaryDesc", "salary"),
				"job_id": _first_value(item, "encryptJobId", "encryptId", "jobId"),
			}
		)
	return rows


def _direction(item: dict[str, Any]) -> str:
	"""消息方向：平台给 from / role / side，缺失时按 isSelf 判定。"""
	value = _first_value(item, "from", "role", "side")
	if isinstance(value, dict):
		value = _first_value(value, "type", "name")
	if isinstance(value, str) and value.strip():
		return value.strip()
	if item.get("isSelf") is True or item.get("self") is True:
		return "我"
	return "对方"


def message_rows(data: Any) -> list[dict[str, Any]]:
	"""聊天记录：方向、内容与时间。"""
	rows: list[dict[str, Any]] = []
	for item in _dict_rows(data, "messageList", "msgList", "messages", "list", "result"):
		rows.append(
			{
				"direction": _direction(item),
				"content": message_text(item),
				"time": format_chat_time(_first_value(item, "createTime", "msgTime", "time", "timestamp")),
			}
		)
	return rows


def last_message_rows(data: Any) -> list[dict[str, Any]]:
	"""``hr last-messages`` 的归一化结果：friendId / unread / msg_status 等。"""
	if not isinstance(data, dict):
		return []
	messages = data.get("messages")
	if not isinstance(messages, list):
		return []
	rows: list[dict[str, Any]] = []
	for item in messages:
		if not isinstance(item, dict):
			continue
		rows.append(
			{
				"friend_id": _first_value(item, "friendId"),
				"unread": _first_value(item, "unread"),
				"status": _first_value(item, "msg_status"),
				"message": _first_value(item, "last_msg"),
				"time": _first_value(item, "last_time"),
			}
		)
	return rows


# ── 渲染入口 ──────────────────────────────────────────────────────────


def render_friend_list(data: Any, *, title: str, next_steps: list[str]) -> None:
	render_list_result(
		friend_list_rows(data),
		title,
		[
			("姓名", "name", "bold cyan"),
			("公司", "company", "green"),
			("职位", "job", "yellow"),
			("未读", "unread", "red"),
			("最近时间", "time", "dim"),
			("最近消息", "message", ""),
		],
		next_steps=next_steps,
	)


def render_candidate_list(data: Any, *, title: str, next_steps: list[str]) -> None:
	render_list_result(
		candidate_list_rows(data),
		title,
		[
			("姓名", "name", "bold cyan"),
			("意向", "expect", "yellow"),
			("城市", "city", "dim"),
			("经验", "experience", "green"),
			("学历", "education", "green"),
			("活跃", "active", "dim"),
		],
		next_steps=next_steps,
	)


def render_job_list(data: Any, *, title: str, next_steps: list[str]) -> None:
	render_list_result(
		job_list_rows(data),
		title,
		[
			("职位", "title", "bold cyan"),
			("状态", "status", "yellow"),
			("城市", "city", "dim"),
			("薪资", "salary", "green"),
			("职位 ID", "job_id", "dim"),
		],
		next_steps=next_steps,
	)


def render_message_history(data: Any, *, title: str, next_steps: list[str]) -> None:
	render_list_result(
		message_rows(data),
		title,
		[
			("方向", "direction", "cyan"),
			("内容", "content", ""),
			("时间", "time", "dim"),
		],
		next_steps=next_steps,
	)


def render_last_messages(data: Any, *, title: str, next_steps: list[str]) -> None:
	render_list_result(
		last_message_rows(data),
		title,
		[
			("候选人", "friend_id", "bold cyan"),
			("未读", "unread", "red"),
			("状态", "status", "yellow"),
			("最近消息", "message", ""),
			("时间", "time", "dim"),
		],
		next_steps=next_steps,
	)


def render_identity(record: Any, *, title: str, next_steps: list[str]) -> None:
	if isinstance(record, dict):
		render_message_panel(record, title=title)
	else:
		render_message_panel({"result": record}, title=title)
	render_next_steps(next_steps)


def render_resume(data: Any, *, title: str, next_steps: list[str]) -> None:
	if isinstance(data, dict):
		render_sectioned_record(data, title=title)
	else:
		render_message_panel({"result": data}, title=title)
	render_next_steps(next_steps)
