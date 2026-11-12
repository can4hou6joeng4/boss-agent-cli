"""Chat 共享常量和安全工具函数。"""

from typing import Any

# relationType 映射：API 返回值 → 可读标签（key 类型放宽为 object，兼容 API 返回 None/未知值走 default）
RELATION_LABELS: dict[object, str] = {1: "对方主动", 2: "我主动", 3: "投递"}
FROM_FILTER = {"boss": 1, "me": 2}
MSG_STATUS_LABELS: dict[object, str] = {1: "未读", 2: "已读"}

# 已知分组渲染顺序
GROUP_ORDER = ["对方主动", "我主动", "投递"]


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
	空列表，避免把畸形载荷当成可迭代的消息集合。
	"""
	if not isinstance(platform_data, dict):
		return []
	for key in ("messages", "historyMsgList"):
		value = platform_data.get(key)
		if isinstance(value, list) and value:
			return value
	return []


def contact_identity(item: dict[str, Any]) -> str:
	"""返回联系人的稳定标识：优先 ``uid``，缺失时回退 ``security_id``。

	``security_id`` 是 BOSS 每次请求轮换的加密令牌，同一个联系人在两次请求中
	会拿到不同的值。因此凡是需要「跨请求比较同一个联系人」的场景
	（快照合并、diff、映射表）都必须以 ``uid`` 为主键，
	仅对缺少 uid 的历史快照数据回退到 ``security_id``。
	"""
	uid = item.get("uid")
	if uid not in (None, ""):
		return str(uid)
	return str(item.get("security_id") or "")


def sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号；过滤 Tab 和回车。"""
	if not isinstance(value, str):
		return str(value)
	value = value.replace("\t", " ").replace("\r", "")
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
		return str(value)
	value = value.replace("\n", " ").replace("\r", "")
	value = value.replace("\\", "\\\\")
	return value.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("<", "\\<")
