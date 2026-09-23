"""Chat 共享常量和安全工具函数。"""

from typing import Any

# relationType 映射：API 返回值 → 可读标签（key 类型放宽为 object，兼容 API 返回 None/未知值走 default）
RELATION_LABELS: dict[object, str] = {1: "对方主动", 2: "我主动", 3: "投递"}
FROM_FILTER = {"boss": 1, "me": 2}
MSG_STATUS_LABELS = {1: "未读", 2: "已读"}

# 已知分组渲染顺序
GROUP_ORDER = ["对方主动", "我主动", "投递"]


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
	"""转义 Markdown 表格中的危险字符。"""
	if not isinstance(value, str):
		return str(value)
	return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")
