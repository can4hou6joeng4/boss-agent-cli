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
	"""
	items: list[dict[str, Any]] = []
	page = start_page
	seen_signatures: set[tuple[str, ...]] = set()

	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return [], resp

		platform_data = platform.unwrap_data(resp) or {}
		page_items = platform_data.get("result") or platform_data.get("friendList") or []
		if not page_items:
			break

		signature = tuple(str(item.get("securityId", "")) for item in page_items if isinstance(item, dict))
		if signature in seen_signatures:
			break
		seen_signatures.add(signature)
		items.extend(page_items)

		if platform_data.get("hasMore") is False:
			break
		page += 1

	return items, None
