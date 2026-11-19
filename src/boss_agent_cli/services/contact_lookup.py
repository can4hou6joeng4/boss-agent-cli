from typing import Any

from boss_agent_cli.services.chat_utils import stable_uid
from boss_agent_cli.services.friend_list_pages import friend_list_page_has_content, friend_list_page_items


class FriendLookupLimitExceeded(RuntimeError):
	"""Raised when paginated friend lookup cannot prove completion safely."""


class FriendListShapeError(RuntimeError):
	"""响应缺少可识别的联系人列表容器，无法判断联系人是否存在（Issue #1978）。"""


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
	- 响应缺少列表容器：抛 FriendListShapeError，不能据此断言联系人不存在
	"""
	try:
		needle = str(identifier).strip()
	except ValueError:
		# 超过 4300 位的整数连 str() 都不可用；平台 uid 不可能是这种值。
		return None, None
	if not needle:
		return None, None

	page = start_page
	terminated = False
	seen_signatures: set[tuple[str, ...]] = set()
	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return None, resp

		platform_data = platform.unwrap_data(resp)
		if not isinstance(platform_data, dict):
			# 页面数据形状漂移（如返回字符串）按空页处理，不把 AttributeError
			# 兜成 INTERNAL_ERROR（Issue #1141 同一口径）。
			platform_data = {}
		page_items = friend_list_page_items(platform_data)
		# 同一页先完整检查稳定 uid，再回退到轮换的 securityId。这样即使某个
		# securityId 恰好与另一条记录的 uid 文本相同，也不会命中错误联系人。
		# uid 只认非空字符串：布尔 / 容器漂移时 str() 会让不同联系人共用
		# 同一文本，匹配到错误对象（Issue #2063）。
		for item in page_items:
			if stable_uid(item.get("uid")) == needle:
				return item, None
		for item in page_items:
			if stable_uid(item.get("securityId")) == needle:
				return item, None

		# securityId 会在请求间轮换，不能用于重复页判定；有 uid 时必须优先用 uid。
		# 签名全为空时无法判断是否重复页，继续翻页，避免把缺字段的新页面
		# 当成重复页提前结束（与 services/friend_list_pages 的聚合守卫一致）。
		identities = [stable_uid(item.get("uid")) or stable_uid(item.get("securityId")) for item in page_items]
		if any(identities):
			signature = tuple(identities)
			if signature in seen_signatures:
				terminated = True
				break
			seen_signatures.add(signature)

		has_more = platform_data.get("hasMore")
		if not page_items:
			if has_more is not False and friend_list_page_has_content(platform_data):
				# 整页元素被跳过：不是空页，继续翻页，不能据此断言联系人不存在
				# （Issue #1878）。
				page += 1
				continue
			if has_more is not False and not _has_list_container(platform_data):
				# 成功码但没有可识别的列表容器（zpData 缺失 / 非 dict / 键为
				# null）：这一页不能证明列表完整，不能落成「联系人不存在」。
				# 真正的空页 ``result: []`` 与显式 hasMore=false 仍按不存在处理。
				raise FriendListShapeError(
					"沟通列表响应不包含可识别的联系人列表，无法确认联系人是否存在；"
					"请刷新沟通列表后重试，不要据此判定联系人不存在"
				)
			terminated = True
			break
		if has_more is False:
			terminated = True
			break
		page += 1

	if not terminated:
		raise FriendLookupLimitExceeded(
			"沟通列表分页遍历超过上限，未能确认联系人是否存在；"
			"请先用 boss chat 取到稳定 uid 再查询，不要重放同一 security_id 查询"
		)
	return None, None


def _has_list_container(platform_data: dict[str, Any]) -> bool:
	"""本页是否带有可识别的列表容器（result / friendList 至少一个是 list）。"""
	return isinstance(platform_data.get("result"), list) or isinstance(platform_data.get("friendList"), list)


# 向后兼容：旧名称保留为别名（匹配逻辑已放宽为 uid 或 security_id）
find_friend_by_security_id = find_friend
