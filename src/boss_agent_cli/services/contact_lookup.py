from typing import Any


class FriendLookupLimitExceeded(RuntimeError):
	"""Raised when paginated friend lookup cannot prove completion safely."""


def find_friend(
	platform: Any,
	identifier: str | int,
	*,
	start_page: int = 1,
	max_pages: int = 50,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	"""分页遍历沟通列表，按 ``uid`` 或 ``security_id`` 查找联系人。

	返回 (friend_item, error_response)：
	- 找到联系人：返回 (item, None)
	- 平台返回失败响应：返回 (None, raw_response)
	- 遍历完成仍未找到：返回 (None, None)
	"""
	needle = str(identifier).strip()
	if not needle:
		return None, None

	page = start_page
	terminated = False
	seen_signatures: set[tuple[str, ...]] = set()
	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return None, resp

		platform_data = platform.unwrap_data(resp) or {}
		items = platform_data.get("result") or platform_data.get("friendList") or []
		dict_items = [item for item in items if isinstance(item, dict)]
		# 同一页先完整检查稳定 uid，再回退到轮换的 securityId。这样即使某个
		# securityId 恰好与另一条记录的 uid 文本相同，也不会命中错误联系人。
		for item in dict_items:
			if str(item.get("uid") or "").strip() == needle:
				return item, None
		for item in dict_items:
			if str(item.get("securityId") or "") == needle:
				return item, None

		# securityId 会在请求间轮换，不能用于重复页判定；有 uid 时必须优先用 uid。
		signature = tuple(str(item.get("uid") or item.get("securityId") or "") for item in dict_items)
		if signature in seen_signatures:
			terminated = True
			break
		seen_signatures.add(signature)

		has_more = platform_data.get("hasMore")
		if not items or has_more is False:
			terminated = True
			break
		page += 1

	if not terminated:
		raise FriendLookupLimitExceeded("沟通列表分页遍历超过上限，未能确认联系人是否存在，请重试")
	return None, None


# 向后兼容：旧名称保留为别名（匹配逻辑已放宽为 uid 或 security_id）
find_friend_by_security_id = find_friend
