"""Chat 共享常量和安全工具函数。"""

import datetime
import math
import re
from typing import Any

from boss_agent_cli.api.models import dict_items, safe_text

# relationType 映射：API 返回值 → 可读标签（key 类型放宽为 object，兼容 API 返回 None/未知值走 default）
RELATION_LABELS: dict[object, str] = {1: "对方主动", 2: "我主动", 3: "投递"}
FROM_FILTER = {"boss": 1, "me": 2}
MSG_STATUS_LABELS: dict[object, str] = {1: "未读", 2: "已读"}

# 已知分组渲染顺序
GROUP_ORDER = ["对方主动", "我主动", "投递"]


def safe_local_time(ts_ms: Any, *, tz: datetime.tzinfo | None = None) -> datetime.datetime | None:
	"""毫秒时间戳转 datetime；越界 / 非有限值返回 None（Issue #1295）。

	平台把纳秒时间戳当毫秒传来、或 JSON 里的 ``1e400`` 变成 ``inf`` 时，
	``datetime.fromtimestamp`` 抛 ``ValueError`` / ``OverflowError``。时间字段
	只是展示信息，坏值按「无时间」处理，不能让整条命令失败——否则
	``handle_auth_errors`` 会把确定性的坏数据兜成可重试的 ``NETWORK_ERROR``，
	Agent 拿着同一份坏数据反复重试、消耗请求预算。
	"""
	try:
		return datetime.datetime.fromtimestamp(ts_ms / 1000, tz=tz)
	except (TypeError, ValueError, OverflowError, OSError):
		return None


def int_code(value: Any) -> int | None:
	"""平台数值编码：只接受真正的整数。

	Python 的 ``bool`` 是 ``int`` 子类，``{1: "未读"}.get(True)`` 会命中编码 1。
	JSON 布尔不携带平台编码语义，不能据此补出「未读」「对方主动」等具体状态，
	也不能当成 `unreadMsgCount` 参与计数（与 Issue #909 的性别编码同因）。
	"""
	return value if type(value) is int else None


def relation_text(value: Any) -> str:
	"""relationType 数值编码的展示文本；漂移值按「未知」（与 CLI 同口径）。"""
	return RELATION_LABELS.get(int_code(value), "未知")


def chat_cutoff_ts(days: int | None, *, now: datetime.datetime | None = None) -> float | None:
	"""`--days N` 的毫秒时间阈值；``None`` 表示不按时间筛选。

	CLI `chat` 与向导 `communication` 必须用同一个「现在」口径，否则同一份
	输入在两处会筛出不同的记录边界（Issue #2667）。
	"""
	if days is None:
		return None
	reference = now if now is not None else datetime.datetime.now()
	return (reference - datetime.timedelta(days=days)).timestamp() * 1000


def matches_chat_filters(
	*,
	initiated_by: str,
	last_ts: Any,
	relation_label: str | None,
	cutoff_ts: float | None,
) -> bool:
	"""单条沟通记录是否命中 `--from` / `--days`；CLI 与向导共用同一判定。

	``last_ts`` 只认真正的整数：旧快照 / 手工改过的记录里字符串、布尔与缺失
	值按「未知 0」处理，且未知时间与 CLI 一样放行——不能把没有时间字段的
	记录全部筛掉（Issue #1317）。
	"""
	if type(last_ts) is not int:
		last_ts = 0
	return (
		(not cutoff_ts or not last_ts or last_ts >= cutoff_ts)
		and (relation_label is None or initiated_by == relation_label)
	)


def message_list(platform_data: Any) -> list[Any]:
	"""取聊天历史响应里的消息数组；``messages`` 优先，兼容 ``historyMsgList``。

	沿用原有「空数组视为缺失」的语义：``messages`` 为空时回退到
	``historyMsgList``。字段缺失、响应非 mapping 或字段类型不是数组时返回
	空列表，避免把畸形载荷当成可迭代的消息集合。数组里的 ``null`` / 数字 /
	字符串元素同样跳过（Issue #1145；与 #1141 同一口径）。
	"""
	if not isinstance(platform_data, dict):
		return []
	for key in ("messages", "historyMsgList"):
		value = dict_items(platform_data.get(key))
		if value:
			return value
	return []


# 超过 4300 位的整数 str() 会抛 ValueError（Python 3.11+ 安全上限）；
# 与 output._MAX_SAFE_INT_BITS 同口径，超过即按缺失处理。
_MAX_UID_INT_BITS = 14284


def stable_uid(value: Any) -> str:
	"""平台 uid 的稳定身份文本：只接受非空字符串或非 bool 整数（Issue #2063 / #2064）。

	真实平台用整数 uid（如 ``10001``），历史快照也有字符串形态，两者都要
	保留。布尔 / 容器 / 浮点漂移时 ``str()`` 会让不同联系人共用 ``"True"``
	/ ``"{}"`` 这类 key——翻页签名误判重复页、整页联系人被当作已读跳过，
	快照合并只留下最后一个；超过 4300 位的整数在 ``str()`` 直接抛
	``ValueError``。漂移值按缺失处理，由调用方回退轮换的 securityId。
	"""
	if isinstance(value, str):
		return value.strip()
	if isinstance(value, int) and not isinstance(value, bool):
		return str(value) if value.bit_length() <= _MAX_UID_INT_BITS else ""
	return ""


def message_text_value(value: Any) -> str | None:
	"""把平台消息文本归一成字符串；布尔 / 容器 / 非有限数值按缺失处理。

	``1e400`` / ``Infinity`` / ``NaN`` 在 JSON 里变成 ``inf`` / ``nan``，裸
	``str()`` 会把 ``"inf"`` / ``"nan"`` 当最后一条消息写进 friendList
	（Issue #2730）。超过 4300 位的整数连 ``str()`` 都会抛 ``ValueError``；
	正常 JSON 解析到不了这里，但服务被直接调用时也不能让整批摘要失败。

	返回 ``None`` 表示「这个键没有可用文本」，调用方据此继续读回退键：
	``a or b`` 会让真值但不是文本的 ``a``（对象 / 数组 / ``true``）吞掉合法的
	``b``（Issue #2749）。招聘者沟通摘要与候选人 ``chatmsg`` 共用这一份。
	"""
	if isinstance(value, bool):
		return None
	if isinstance(value, str):
		return value or None
	if isinstance(value, int):
		return str(value) if value.bit_length() <= _MAX_UID_INT_BITS else None
	if isinstance(value, float):
		return str(value) if math.isfinite(value) else None
	return None


# 聊天历史的展示标签；CLI `chatmsg` 与向导 `candidate_chat_history` 共用。
_MSG_TYPE_MAP = {
	1: "文本", 2: "图片", 3: "招呼", 4: "简历", 5: "系统",
	6: "名片", 7: "语音", 8: "视频", 9: "表情",
}


def message_type_label(msg_type: Any) -> str:
	"""平台消息类型编码的可读标签；布尔不是类型编码（Issue #915）。"""
	code = int_code(msg_type)
	if code is not None:
		return _MSG_TYPE_MAP.get(code, f"其他({code})")
	return f"其他({msg_type})"


def _message_body(msg: dict[str, Any]) -> dict[str, Any]:
	body = msg.get("body")
	return body if isinstance(body, dict) else {}


def _message_text(msg: dict[str, Any], body: dict[str, Any]) -> str:
	"""消息正文：按顶层 ``text`` → ``body.text`` 取第一个可用文本，恒为字符串。

	真值但不是文本的顶层 ``text``（对象 / 数组 / ``true``）不能吞掉合法的
	``body.text``；坏值也不应原样进信封（Issue #2749）。
	"""
	values = (msg.get("text"), body.get("text"))
	for value in values:
		if isinstance(value, str) and value:
			return value
	for value in values:
		text = message_text_value(value)
		if text is not None:
			return text
	return ""


def _first_present(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> Any:
	for source in (primary, secondary):
		for key in keys:
			value = source.get(key)
			if value not in (None, ""):
				return value
	return None


def _extract_links(msg: dict[str, Any], *, body: dict[str, Any]) -> list[str]:
	links: list[str] = []
	# 用显式栈而不是递归：消息体可能把 URL 放在 list 套 list / dict 的任意层级，
	# 旧实现遇到嵌套 list 就停止（Issue #2886）；显式栈同时避开深嵌套的
	# RecursionError。reverse 入栈保持原有的深度优先顺序，最后统一去重。
	stack = [value for value in reversed(list(msg.values()) + list(body.values()))]
	while stack:
		value = stack.pop()
		if isinstance(value, str):
			links.extend(re.findall(r"https?://[^\s\"'<>]+", value))
		elif isinstance(value, dict):
			stack.extend(reversed(list(value.values())))
		elif isinstance(value, list):
			stack.extend(reversed(value))
	return list(dict.fromkeys(links))


def normalize_chat_message(
	msg: dict[str, Any],
	*,
	gid: str,
	friend_name: str,
	include_raw: bool = False,
) -> dict[str, Any]:
	"""把平台聊天消息归一成 CLI `chatmsg` 的输出形状（Issue #2801）。

	向导 `candidate_chat_history` 与 CLI 共用这一份，避免同一条消息在两条
	入口给出不同字段。``from`` 只凭稳定的非空 uid 判定，缺失按「未知」；
	时间只接受真正的时间戳，坏值按「无时间」处理。
	"""
	from_obj = msg.get("from", {})
	sender_uid = ""
	sender_name = ""
	if isinstance(from_obj, dict):
		sender_uid = stable_uid(from_obj.get("uid"))
		raw_sender_name = from_obj.get("name")
		if isinstance(raw_sender_name, str):
			sender_name = raw_sender_name.strip()
	if not sender_uid:
		from_name = "未知"
	elif sender_uid == gid:
		from_name = sender_name or friend_name
	else:
		from_name = "我"

	msg_time = ""
	ts = msg.get("time")
	if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts:
		msg_dt = safe_local_time(ts)
		msg_time = msg_dt.strftime("%m-%d %H:%M") if msg_dt is not None else ""

	body = _message_body(msg)
	normalized: dict[str, Any] = {
		"from": from_name,
		"type": message_type_label(msg.get("type")),
		"text": _message_text(msg, body),
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


def contact_identity(item: dict[str, Any]) -> str:
	"""返回联系人的稳定标识：优先 ``uid``，缺失时回退 ``security_id``。

	``security_id`` 是 BOSS 每次请求轮换的加密令牌，同一个联系人在两次请求中
	会拿到不同的值。因此凡是需要「跨请求比较同一个联系人」的场景
	（快照合并、diff、映射表）都必须以 ``uid`` 为主键，
	仅对缺少 uid 的历史快照数据回退到 ``security_id``。

	两个字段都只接受非空字符串：布尔 / 容器 / 数字漂移曾让不同联系人共用
	同一 identity，快照合并时互相覆盖（Issue #2064）。
	"""
	uid = stable_uid(item.get("uid"))
	if uid:
		return uid
	return stable_uid(item.get("security_id"))


#: 会被 Excel / openpyxl 当成公式开头的字符。
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")

#: 表格软件可能忽略的前导控制字符；紧随其后的公式前缀同样危险（Issue #2459）。
_CSV_IGNORED_LEADING = "\n\x0b\x0c\x00﻿"


def sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号；过滤 Tab 和回车。

	前缀判定先跳过前导换行 / VT / FF / NUL / BOM：``"\\n=cmd"`` 这类单元格
	在部分表格软件里会忽略前导控制字符后按公式求值，只看 ``value[0]`` 会放行
	（Issue #2459）。跳过只用于判定，不改写原文。
	"""
	if not isinstance(value, str):
		# 漂移值先转成字符串，再走与 str 完全相同的清洗链：直接 return
		# str(value) 会跳过 Tab/回车过滤与公式前缀检查，超大整数的 str()
		# 还会抛 ValueError（Issue #2198）。
		value = safe_text(value)
	value = value.replace("\t", " ").replace("\r", "")
	candidate = value.lstrip(_CSV_IGNORED_LEADING)
	# 单独一个 ``-`` 是导出里的缺失占位符，不是公式（Issue #1793）。
	if candidate == "-":
		return value
	if candidate and candidate[0] in _CSV_FORMULA_PREFIXES:
		return f"'{value}"
	return value


def escape_md_cell(value: str) -> str:
	"""转义 Markdown 表格与正文中的危险字符。

	除表格分隔符 ``|`` 与换行外，还要打断链接 / 图片 / 自动链接 / 原始 HTML
	语法：公司名、职位名与聊天内容都是平台外部输入，``[点我](https://...)``
	在导出的 Markdown（日报常被直接转发到邮件 / 飞书）里会变成可点击链接，
	``<https://...>`` 与 ``<img ...>`` 同理。反斜杠先转义，避免与后续转义
	叠加成双重转义。正常文本不受影响：Markdown 里转义字符渲染成原字符。
	"""
	if not isinstance(value, str):
		# 平台把字段漂移成列表 / 对象时，其文本表示同样带 ``|`` 与链接语法：
		# 先转成字符串再走同一套转义（Issue #1792）。``safe_text`` 兜住超大
		# 整数的 ``str()`` 上限（Issue #2198）。
		value = safe_text(value)
	value = value.replace("\n", " ").replace("\r", "")
	value = value.replace("\\", "\\\\")
	return value.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("<", "\\<")
