from typing import Any


class FriendLookupLimitExceeded(RuntimeError):
	"""Raised when paginated friend lookup cannot prove completion safely."""


def _identifier_matches(item: dict[str, Any], identifier: str) -> bool:
	"""判断沟通列表条目是否对应目标标识。

	优先按数值 ``uid`` 匹配——它是跨请求稳定的唯一标识。
	``securityId`` 仅作兜底：该值是 BOSS 每次请求轮换的加密令牌，
	同一个联系人在两次请求中会拿到不同的 ``securityId``，
	因此它**不能**作为唯一的匹配依据（否则必然找不到联系人）。
	"""
	if str(item.get("uid") or "").strip() == identifier:
		return True
	return str(item.get("securityId") or "") == identifier


def find_friend(
	platform: Any,
	identifier: str,
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
		for item in items:
			if isinstance(item, dict) and _identifier_matches(item, needle):
				return item, None

		signature = tuple(str(item.get("securityId", "")) for item in items if isinstance(item, dict))
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


def resolve_friend_or_emit(
	ctx: Any,
	command: str,
	platform: Any,
	identifier: str,
	*,
	not_found_message: str | None = None,
) -> dict[str, Any] | None:
	"""按 uid（或 security_id）定位联系人；失败时输出统一错误信封并返回 None。

	封装 mark/exchange/chatmsg/chat-summary 共用的「解析 + 错误处理」样板：
	NotImplementedError→NOT_SUPPORTED、FriendLookupLimitExceeded→NETWORK_ERROR、
	平台失败→按错误码、未找到→JOB_NOT_FOUND。成功返回 friend_item；否则输出错误信封
	并返回 None（调用方应立即 return）。

	注意：调用方**不要**把 identifier 直接当作 securityId 传给平台 API——
	securityId 是每请求轮换的令牌，必须使用本次 friend_list 返回的
	``friend_item["securityId"]``。
	"""
	from boss_agent_cli.display import handle_error_output, handle_not_supported, handle_platform_error_output

	try:
		friend_item, friends_error = find_friend(platform, identifier)
	except NotImplementedError as exc:
		handle_not_supported(ctx, command, exc, fallback_message="当前平台不支持沟通列表能力")
		return None
	except FriendLookupLimitExceeded as exc:
		handle_error_output(
			ctx,
			command,
			code="NETWORK_ERROR",
			message=str(exc),
			recoverable=True,
			recovery_action="重试",
		)
		return None
	if friends_error is not None:
		handle_platform_error_output(ctx, command, platform, friends_error, fallback_message="沟通列表获取失败")
		return None
	if friend_item is None:
		handle_error_output(
			ctx,
			command,
			code="JOB_NOT_FOUND",
			message=not_found_message or f"未在沟通列表中找到联系人 {identifier}",
		)
		return None
	return friend_item
