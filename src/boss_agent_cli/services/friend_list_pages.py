from typing import Any


MAX_FRIEND_LIST_PAGES = 50


def collect_friend_list_items(
	platform: Any,
	*,
	start_page: int = 1,
	max_pages: int = MAX_FRIEND_LIST_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
	"""安全聚合多页沟通列表，保留原始顺序。

	返回 (items, error_response)：
	- 成功聚合：([items...], None)
	- 任一页平台失败：([], raw_response)

	调用方需要判断结果是否完整时，用 :func:`collect_friend_list_page`。
	"""
	items, error, _pages_scanned, _truncated = collect_friend_list_page(
		platform, start_page=start_page, max_pages=max_pages
	)
	return items, error


def collect_friend_list_page(
	platform: Any,
	*,
	start_page: int = 1,
	max_pages: int = MAX_FRIEND_LIST_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, int, bool]:
	"""聚合多页沟通列表，并显式报告是否因页数上限而截断。

	返回 (items, error_response, pages_scanned, truncated)：

	- 翻到平台 ``hasMore=False``、重复页或空页：``truncated=False``，结果完整；
	- 翻满 ``max_pages`` 且平台仍声称有下一页：``truncated=True``，items 只是前
	  ``max_pages`` 页。调用方必须把这一点告诉 Agent / 用户，不能当成完整统计
	  （Issue #885）。
	"""
	items: list[dict[str, Any]] = []
	page = start_page
	seen_signatures: set[tuple[str, ...]] = set()
	pages_scanned = 0
	truncated = False

	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		pages_scanned += 1
		if not platform.is_success(resp):
			return [], resp, pages_scanned, False

		platform_data = platform.unwrap_data(resp) or {}
		page_items = platform_data.get("result") or platform_data.get("friendList") or []
		if not page_items:
			break

		# securityId 在每次请求间都会轮换（见 services/contact_lookup.py），
		# 只能作为 uid 缺失时的回退标识；签名全为空时无法判断是否重复，
		# 继续聚合，避免把缺字段的新页面当成重复页丢掉。
		identities = [
			str(item.get("uid") or item.get("securityId") or "")
			for item in page_items
			if isinstance(item, dict)
		]
		if any(identities):
			signature = tuple(identities)
			if signature in seen_signatures:
				break
			seen_signatures.add(signature)
		items.extend(page_items)

		if platform_data.get("hasMore") is False:
			break
		if pages_scanned >= max_pages:
			# 上限耗尽而平台仍未说「没有下一页」：结果只是前缀，必须显式标记。
			truncated = True
		page += 1

	return items, None, pages_scanned, truncated
