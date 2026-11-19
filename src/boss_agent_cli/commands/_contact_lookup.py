"""按 uid / security_id 定位联系人并在失败时输出统一错误信封（mark/exchange/chatmsg/chat-summary 共用）。"""

from typing import Any

from boss_agent_cli.services.contact_lookup import FriendListShapeError, FriendLookupLimitExceeded, find_friend


def current_friend_security_id_or_emit(
	ctx: Any,
	command: str,
	friend_item: dict[str, Any],
) -> str | None:
	"""读取本次 friend_list 返回的 securityId；缺失时安全停止。

	调用方传入的标识可能是 uid，绝不能在 securityId 缺失时把 uid 猜作
	securityId，尤其不能用于联系方式交换等写请求。
	"""
	from boss_agent_cli.display import boss_command_for_ctx, handle_error_output

	# 只认字符串：类型漂移成对象 / 布尔时 str() 会把 Python repr 当令牌发给
	# 写请求（Issue #1812）。
	raw_security_id = friend_item.get("securityId") or friend_item.get("security_id")
	security_id = raw_security_id.strip() if isinstance(raw_security_id, str) else ""
	if security_id:
		return security_id
	# 与本文件 current_friend_uid_or_emit 同一口径：这是本次列表数据的确定性
	# 缺失，重放同一段查询不会改变结果，不能报成可重试的网络错误（Issue #1504）。
	chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
	handle_error_output(
		ctx,
		command,
		code="NETWORK_ERROR",
		message="沟通列表返回的联系人缺少当前 securityId，已停止执行；按当前列表重试不会改变结果",
		recoverable=False,
		recovery_action=f"用 {chat_cmd} 刷新沟通列表并取到联系人 security_id 后再执行 {command}",
		hints={"next_actions": [chat_cmd]},
	)
	return None


def current_friend_uid_or_emit(
	ctx: Any,
	command: str,
	friend_item: dict[str, Any],
) -> str | None:
	"""读取本次 friend_list 返回的稳定 uid；缺失时安全停止。

	``uid`` 键存在但为 null 时，旧实现 ``str(item.get("uid", ""))`` 会得到
	字符串 ``"None"``，被当作 gid 发请求或写进 uniqueId / friendId
	（Issue #1352）。空 uid 不是可用的联系人身份：必须停止，而不是把空值
	或占位文本发给平台。
	"""
	from boss_agent_cli.display import boss_command_for_ctx, handle_error_output

	# uid 实测是整数或数字串；布尔与容器不是联系人身份，不能 str() 成
	# "True" / "{'a': 1}" 发给平台（Issue #1812）。
	raw_uid = friend_item.get("uid")
	if isinstance(raw_uid, bool) or not isinstance(raw_uid, (str, int)):
		raw_uid = ""
	uid = str(raw_uid or "").strip()
	if uid:
		return uid
	chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
	handle_error_output(
		ctx,
		command,
		code="NETWORK_ERROR",
		message="沟通列表返回的联系人缺少稳定 uid，已停止执行；按当前列表重试不会改变结果",
		recoverable=False,
		recovery_action=f"用 {chat_cmd} 刷新沟通列表并取到联系人 uid 后再执行 {command}",
		hints={"next_actions": [chat_cmd]},
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
	平台失败→按错误码、未找到→CONTACT_NOT_FOUND。成功返回 friend_item；否则输出错误信封
	并返回 None（调用方应立即 return）。

	注意：调用方**不要**把 identifier 直接当作 securityId 传给平台 API——
	securityId 是每请求轮换的令牌，必须使用本次 friend_list 返回的
	``friend_item["securityId"]``。
	"""
	from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_not_supported, handle_platform_error_output

	# 空标识不可能命中任何联系人：继续下去会先把整个沟通列表翻一遍再报
	# CONTACT_NOT_FOUND，既浪费一次受限流的请求，也让真正的原因（argv 少了
	# 参数）看起来像「联系人不在这份列表里」。
	if not str(identifier or "").strip():
		chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
		handle_error_output(
			ctx,
			command,
			code="INVALID_PARAM",
			message="联系人标识不能为空；先用 boss chat 取到 uid 或 security_id",
			recovery_action=f"用 {chat_cmd} 取到联系人的 uid 或 security_id 后重试",
			hints={"next_actions": [chat_cmd]},
		)
		return None
	try:
		friend_item, friends_error = find_friend(platform, identifier)
	except NotImplementedError as exc:
		handle_not_supported(ctx, command, exc, fallback_message="当前平台不支持沟通列表能力")
		return None
	except FriendListShapeError as exc:
		# 响应缺少列表容器是可恢复的读取异常：刷新列表后可以重试，但不能把
		# 不可判定的响应当成「联系人不存在」（Issue #1978）。
		chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
		handle_error_output(
			ctx,
			command,
			code="NETWORK_ERROR",
			message=str(exc),
			recovery_action=f"用 {chat_cmd} 刷新沟通列表后重试；响应缺少列表容器时不能判定联系人是否存在",
			hints={"next_actions": [chat_cmd]},
		)
		return None
	except FriendLookupLimitExceeded as exc:
		# 分页上限是确定性结果：按会轮换的 securityId 重试同一段查询不会改变
		# 结论。与 wizard 的 _candidate_friend 同一口径，给出可执行的 uid 路径
		# （Issue #1353）；recoverable 必须是 false，否则按 recoverable 决策的
		# 自动重试会再翻满 50 页并消耗请求预算（Issue #1549）。
		chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
		handle_error_output(
			ctx,
			command,
			code="NETWORK_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=(
				f"先用 {chat_cmd} 确认联系人仍在沟通列表并取到 uid，再用 uid 重试；"
				"按会轮换的 securityId 查询超过分页上限时，重试同样的查询不会改变结果"
			),
			hints={"next_actions": [chat_cmd]},
		)
		return None
	if friends_error is not None:
		handle_platform_error_output(ctx, command, platform, friends_error, fallback_message="沟通列表获取失败")
		return None
	if friend_item is None:
		chat_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
		handle_error_output(
			ctx,
			command,
			code="CONTACT_NOT_FOUND",
			message=not_found_message or f"未在沟通列表中找到联系人 {identifier}",
			hints={"next_actions": [chat_cmd]},
		)
		return None
	return friend_item
