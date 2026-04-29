from typing import Any


def find_friend_by_security_id(
	platform: Any,
	security_id: str,
	*,
	start_page: int = 1,
	max_pages: int = 50,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	"""分页遍历沟通列表，按 security_id 查找联系人。

	返回 (friend_item, error_response)：
	- 找到联系人：返回 (item, None)
	- 平台返回失败响应：返回 (None, raw_response)
	- 遍历完成仍未找到：返回 (None, None)
	"""
	page = start_page
	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return None, resp

		platform_data = platform.unwrap_data(resp) or {}
		items = platform_data.get("result") or platform_data.get("friendList") or []
		for item in items:
			if item.get("securityId") == security_id:
				return item, None

		has_more = platform_data.get("hasMore")
		if not items or has_more is False:
			break
		page += 1

	return None, None
