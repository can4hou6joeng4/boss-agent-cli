"""从平台列表响应推导信封 pagination 的共享逻辑。

只有平台返回布尔 ``hasMore`` 时才给出 ``true`` / ``false``；缺失或类型不
明确时为 ``None`` 并把来源标成 ``unknown``——否则 Agent 会把「平台没说」
读成「确实没有下一页」，提前停止翻页。``total`` 始终是本次返回条数，
不代表平台全集。
"""
from typing import Any, Mapping

UNKNOWN_HAS_MORE_OPERATOR_ACTION = "平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"

#: 下一页提示里可安全递增的页码上限。平台把 ``page`` 写成超长整数时，
#: ``page + 1`` 的字符串化会撞上整数转换上限并报成可重试错误（Issue #1824）。
MAX_TRUSTED_PAGE = 1_000_000


def list_pagination(
	platform_data: Any,
	*,
	page: int,
	rows: list[Any] | None = None,
) -> dict[str, Any]:
	"""构造列表命令的分页证据；``page`` 不可信时回显请求页。"""
	is_mapping = isinstance(platform_data, dict)
	raw_page = platform_data.get("page") if is_mapping else None
	if isinstance(raw_page, int) and not isinstance(raw_page, bool) and 1 <= raw_page <= MAX_TRUSTED_PAGE:
		current_page = raw_page
	elif isinstance(page, int) and not isinstance(page, bool):
		# 回退的请求页也必须钳制：九个调用点会用 ``pagination["page"] + 1``
		# 拼下一页提示，超长整数在这里字符串化会抛 ValueError（Issue #1880）。
		current_page = min(max(page, 1), MAX_TRUSTED_PAGE)
	else:
		current_page = 1
	raw_has_more = platform_data.get("hasMore") if is_mapping else None
	has_more = raw_has_more if isinstance(raw_has_more, bool) else None
	return {
		"page": current_page,
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": len(rows) if rows is not None else None,
		"total_kind": "returned_page_count",
	}


def page_can_advance(pagination: Mapping[str, Any]) -> bool:
	"""下一页提示是否还能前进：页码达到可信上限时返回 false。

	九个调用点用 ``pagination["page"] + 1`` 拼下一页提示；钳制后的页码再加一
	只会得到同一页并被再次钳制，Agent 会无界重复同一请求（Issue #2434，与
	history 的 #2012 同口径）。``has_more`` 保留平台原始证据，不改写。
	"""
	page = pagination.get("page")
	return isinstance(page, int) and not isinstance(page, bool) and page < MAX_TRUSTED_PAGE


def rows_from(platform_data: Any, key: str) -> list[Any] | None:
	"""取平台响应里的列表字段；缺失或类型不符时返回 ``None``。"""
	value = platform_data.get(key) if isinstance(platform_data, dict) else None
	return value if isinstance(value, list) else None
