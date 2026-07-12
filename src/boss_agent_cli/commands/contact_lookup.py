from typing import Any


class FriendLookupLimitExceeded(RuntimeError):
	"""Raised when paginated friend lookup cannot prove completion safely."""


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
	terminated = False
	seen_signatures: set[tuple[str, ...]] = set()
	for _ in range(max_pages):
		resp = platform.friend_list(page=page)
		if not platform.is_success(resp):
			return None, resp

		platform_data = platform.unwrap_data(resp) or {}
		items = platform_data.get("result") or platform_data.get("friendList") or []
		for item in items:
			if item.get("securityId") == security_id:
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


def resolve_friend_or_emit(
	ctx: Any,
	command: str,
	platform: Any,
	security_id: str,
	*,
	not_found_message: str | None = None,
) -> dict[str, Any] | None:
	"""按 security_id 定位联系人；失败时输出统一错误信封并返回 None。

	封装 mark/exchange/chatmsg/chat-summary 共用的「解析 + 错误处理」样板：
	NotImplementedError→NOT_SUPPORTED、FriendLookupLimitExceeded→NETWORK_ERROR、
	平台失败→按错误码、未找到→JOB_NOT_FOUND。成功返回 friend_item；否则输出错误信封
	并返回 None（调用方应立即 return）。
	"""
	from boss_agent_cli.display import handle_error_output, handle_not_supported, handle_platform_error_output

	try:
		friend_item, friends_error = find_friend_by_security_id(platform, security_id)
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
			message=not_found_message or f"未在沟通列表中找到 security_id={security_id}",
		)
		return None
	return friend_item
