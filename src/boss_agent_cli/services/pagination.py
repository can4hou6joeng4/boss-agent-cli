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

#: 单页数量与页码共用同一个可信上限：只阻止无界整数进入平台参数 / 本地
#: JSON（整数转字符串的 4300 位限制），不代表平台接受这么大的值。
MAX_TRUSTED_COUNT = MAX_TRUSTED_PAGE

#: `export` 的页数预算估算：平台每页约 15 条，再加固定余量给本地预筛 /
#: 福利 / 活跃度筛选剔除的整页结果。顶层 `boss export` 与 wizard / MCP 的
#: `export` goal 共用同一口径，避免同一个 count 在两处导出不同数量。
EXPORT_PAGE_SIZE_ESTIMATE = 15
EXPORT_FILTER_PAGE_ALLOWANCE = 5

#: `export` 的默认请求条数（CLI `--count` 的默认值）。向导 / MCP 的 export
#: goal 在带福利 / 活跃度筛选且没给 count 时也必须按这个值扫描，否则管线会
#: 按搜索的 10 条活跃度目标提前停止并静默导出不完整结果（Issue #2716）。
DEFAULT_EXPORT_COUNT = 50


def export_page_budget(count: int) -> int:
	"""按请求条数估算需要扫描的页数上限（含筛选余量）。"""
	estimated_pages = max(1, (count + EXPORT_PAGE_SIZE_ESTIMATE - 1) // EXPORT_PAGE_SIZE_ESTIMATE)
	return estimated_pages + EXPORT_FILTER_PAGE_ALLOWANCE


def clamp_trusted(value: Any, limit: int = MAX_TRUSTED_PAGE) -> int:
	"""把外部数值钳制到 ``[1, limit]``；非整数（含布尔）按 1。

	直接调用命令回调时不经过 Click 的 ``IntRange``。
	超长整数一旦进入 httpx 参数序列化或 ``page + 1`` 提示，就会撞上
	Python 3.11+ 的整数转字符串上限（Issue #2550）。
	"""
	if not isinstance(value, int) or isinstance(value, bool):
		return 1
	return min(max(value, 1), limit)


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


def rows_from_first(platform_data: Any, *keys: str) -> list[Any] | None:
	"""按优先级取第一个可识别的列表容器；都不是列表时返回 ``None``。

	终端渲染与 ``pagination.total`` 必须共用同一组候选键：只数固定键时，平台把
	列表换到 ``result`` / ``items`` / ``jobList`` 等替代容器，信封里明明有记录
	却报 ``total=None`` / ``0``（Issue #3034）。空列表也是可识别容器，命中即返回，
	不能继续找兄弟键。
	"""
	if not isinstance(platform_data, dict):
		return None
	for key in keys:
		value = platform_data.get(key)
		if isinstance(value, list):
			return value
	return None
