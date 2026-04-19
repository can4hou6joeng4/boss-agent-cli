"""Chat 共享常量和安全工具函数。"""

# relationType 映射：API 返回值 → 可读标签（key 类型放宽为 object，兼容 API 返回 None/未知值走 default）
RELATION_LABELS: dict[object, str] = {1: "对方主动", 2: "我主动", 3: "投递"}
FROM_FILTER = {"boss": 1, "me": 2}
MSG_STATUS_LABELS = {1: "未读", 2: "已读"}

# 已知分组渲染顺序
GROUP_ORDER = ["对方主动", "我主动", "投递"]


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
