"""Chat 共享常量和安全工具函数。"""

import datetime
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


def sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号；过滤 Tab 和回车。"""
	if not isinstance(value, str):
		# 漂移值先转成字符串，再走与 str 完全相同的清洗链：直接 return
		# str(value) 会跳过 Tab/回车过滤与公式前缀检查，超大整数的 str()
		# 还会抛 ValueError（Issue #2198）。
		value = safe_text(value)
	value = value.replace("\t", " ").replace("\r", "")
	# 单独一个 ``-`` 是导出里的缺失占位符，不是公式（Issue #1793）。
	if value == "-":
		return value
	if value and value[0] in ("=", "+", "-", "@"):
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
