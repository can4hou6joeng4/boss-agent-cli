"""从平台列表响应推导信封 pagination 的共享逻辑。

只有平台返回布尔 ``hasMore`` 时才给出 ``true`` / ``false``；缺失或类型不
明确时为 ``None`` 并把来源标成 ``unknown``——否则 Agent 会把「平台没说」
读成「确实没有下一页」，提前停止翻页。``total`` 始终是本次返回条数，
不代表平台全集。
"""
from typing import Any

UNKNOWN_HAS_MORE_OPERATOR_ACTION = "平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"


def list_pagination(
	platform_data: Any,
	*,
	page: int,
	rows: list[Any] | None = None,
) -> dict[str, Any]:
	"""构造列表命令的分页证据；``page`` 不可信时回显请求页。"""
	is_mapping = isinstance(platform_data, dict)
	raw_page = platform_data.get("page") if is_mapping else None
	current_page = (
		raw_page
		if isinstance(raw_page, int) and not isinstance(raw_page, bool) and raw_page >= 1
		else page
	)
	raw_has_more = platform_data.get("hasMore") if is_mapping else None
	has_more = raw_has_more if isinstance(raw_has_more, bool) else None
	return {
		"page": current_page,
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": len(rows) if rows is not None else None,
		"total_kind": "returned_page_count",
	}


def rows_from(platform_data: Any, key: str) -> list[Any] | None:
	"""取平台响应里的列表字段；缺失或类型不符时返回 ``None``。"""
	value = platform_data.get(key) if isinstance(platform_data, dict) else None
	return value if isinstance(value, list) else None
