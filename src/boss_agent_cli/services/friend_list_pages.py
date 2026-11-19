from typing import Any

from boss_agent_cli.api.models import dict_items


MAX_FRIEND_LIST_PAGES = 50


def friend_list_page_items(platform_data: dict[str, Any]) -> list[dict[str, Any]]:
	"""取沟通列表页里的联系人条目。

	``result`` 只有能取出至少一个对象元素时才优先使用；平台把 ``result`` 漂移成
	对象 / 数字 / 非空字符串时，``result or friendList`` 会选中这个真值容器，
	``dict_items`` 返回空列表而合法的 ``friendList`` 永远不会被读取（Issue #1351）。
	``result`` 是 ``[None]`` / ``[5]`` 这类只含坏元素的非空列表时同理（Issue #1402）：
	元素级漂移由 ``dict_items`` 跳过，跳过之后为空就回退 ``friendList``。
	"""
	return dict_items(platform_data.get("result")) or dict_items(platform_data.get("friendList"))


def friend_list_page_has_content(platform_data: dict[str, Any]) -> bool:
	"""页面容器是否带有内容（哪怕元素全被跳过）。

	``friend_list_page_items`` 返回空列表有两种含义：平台本来就给了空页，或
	容器非空但元素全是 null / 数字 / 字符串。后者不是「列表结束」，翻页方
	据此继续请求，不能宣称结果完整或联系人不存在（Issue #1878）。
	"""
	for key in ("result", "friendList"):
		raw = platform_data.get(key)
		if isinstance(raw, list):
			if raw:
				return True
		elif raw is not None:
			return True
	return False


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

		platform_data = platform.unwrap_data(resp)
		if not isinstance(platform_data, dict):
			platform_data = {}
		# 列表元素混入 null / 数字 / 字符串时只跳过坏元素（Issue #1141 同一口径），
		# 否则下游 build_pipeline_items / _friend_record 读键会整条命令崩溃。
		page_items = friend_list_page_items(platform_data)
		if not page_items:
			if platform_data.get("hasMore") is not False and friend_list_page_has_content(platform_data):
				# 整页元素被跳过：不是空页，继续翻页（Issue #1878）。
				page += 1
				continue
			break

		# securityId 在每次请求间都会轮换（见 services/contact_lookup.py），
		# 只能作为 uid 缺失时的回退标识；签名全为空时无法判断是否重复，
		# 继续聚合，避免把缺字段的新页面当成重复页丢掉。
		identities = [str(item.get("uid") or item.get("securityId") or "") for item in page_items]
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
