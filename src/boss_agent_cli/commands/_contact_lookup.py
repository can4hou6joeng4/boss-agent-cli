"""按 uid / security_id 定位联系人并在失败时输出统一错误信封（mark/exchange/chatmsg/chat-summary 共用）。"""

from typing import Any

from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded, find_friend


def current_friend_security_id_or_emit(
	ctx: Any,
	command: str,
	friend_item: dict[str, Any],
) -> str | None:
	"""读取本次 friend_list 返回的 securityId；缺失时安全停止。

	调用方传入的标识可能是 uid，绝不能在 securityId 缺失时把 uid 猜作
	securityId，尤其不能用于联系方式交换等写请求。
	"""
	from boss_agent_cli.display import handle_error_output

	security_id = str(friend_item.get("securityId") or friend_item.get("security_id") or "").strip()
	if security_id:
		return security_id
	handle_error_output(
		ctx,
		command,
		code="NETWORK_ERROR",
		message="沟通列表返回的联系人缺少当前 securityId，已停止执行；请刷新沟通列表后重试",
	)
	return None


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
