from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._contact_lookup import current_friend_security_id_or_emit, current_friend_uid_or_emit, resolve_friend_or_emit
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._text_guard import reject_unencodable_params
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_not_supported,
	handle_output,
	handle_platform_error_output,
	positional_hint,
	render_simple_list,
	shell_argument,
)
from boss_agent_cli.services.chat_utils import message_list, normalize_chat_message as _normalize_message
from boss_agent_cli.services.contact_lookup import friend_display_name
from boss_agent_cli.services.pagination import (
	MAX_TRUSTED_COUNT,
	MAX_TRUSTED_PAGE,
	UNKNOWN_HAS_MORE_OPERATOR_ACTION,
	clamp_trusted,
	list_pagination,
	page_can_advance,
)

@click.command("chatmsg")
@click.argument("security_id", metavar="UID_OR_SECURITY_ID")
@click.option(
	"--friend-uid",
	default=None,
	help=(
		"已知稳定 uid（取自 boss chat）时直接读取消息：此时位置参数按本次请求的 "
		"security_id 使用，跳过沟通列表扫描。security_id 过期时按平台错误返回，不退回全列表查找"
	),
)
@click.option("--page", default=1, type=click.IntRange(min=1, max=MAX_TRUSTED_PAGE), help="页码")
@click.option("--count", default=20, type=click.IntRange(min=1, max=MAX_TRUSTED_COUNT), help="每页消息数量")
@click.option("--raw", "show_raw", is_flag=True, default=False, help="输出保真结构化消息字段")
@click.pass_context
@handle_auth_errors("chatmsg")
def chatmsg_cmd(
	ctx: click.Context,
	security_id: str,
	friend_uid: str | None,
	page: int,
	count: int,
	show_raw: bool,
) -> None:
	"""查看与指定好友的聊天消息历史

	SECURITY_ID 可传 boss chat 输出的 uid（推荐，跨请求稳定）或 security_id。
	已知 uid 与本次 security_id 时可加 --friend-uid 跳过沟通列表扫描。
	"""
	if not require_compliance_allowed(ctx, "chatmsg"):
		ctx.exit(1)

	# 直读路径会把 security_id 原样发给 chat/history：孤立代理项会在 httpx
	# 编码阶段失败，入口先按参数错误拒绝（Issue #2533）。
	if reject_unencodable_params(ctx, "chatmsg", security_id=security_id, **{"--friend-uid": friend_uid}):
		return

	# 直接调用回调也要限制页码和数量，避免超长整数进入请求序列化（Issue #2550）。
	page = clamp_trusted(page)
	count = clamp_trusted(count, MAX_TRUSTED_COUNT)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		direct_uid = ""
		if friend_uid is not None:
			# 显式给了空值不能静默退回全列表扫描：那样调用方以为走了直读路径，
			# 实际又在翻页（与 MCP 参数「truthiness 吞显式空值」同一口径）。
			direct_uid = friend_uid.strip()
			if not direct_uid:
				handle_error_output(
					ctx,
					"chatmsg",
					code="INVALID_PARAM",
					message="--friend-uid 不能为空；省略该选项时按 uid / security_id 查找联系人",
					recovery_action="传入 boss chat 输出的稳定 uid，或去掉 --friend-uid 后用 uid / security_id 查找",
				)
				return
		if direct_uid:
			if not (security_id or "").strip():
				handle_error_output(
					ctx,
					"chatmsg",
					code="INVALID_PARAM",
					message="使用 --friend-uid 时位置参数必须是本次请求的 security_id，不能为空",
					recovery_action="补上 boss chat 输出的 security_id，或去掉 --friend-uid 改用 uid 查找",
				)
				return
			# 直读路径：调用方声明 uid 与 security_id 有效，不再遍历沟通列表
			# （Issue #2277）。securityId 过期时平台会直接报错，按实际错误返回，
			# 不隐式退回全列表扫描。
			gid = direct_uid
			fresh_security_id = security_id.strip()
			friend_name = "-"
		else:
			friend_item = resolve_friend_or_emit(
				ctx,
				"chatmsg",
				platform,
				security_id,
				not_found_message=(
					f"未在沟通列表中找到联系人 {security_id}，请确认该联系人存在"
					"（可用 boss chat 输出的 uid 或 security_id）"
				),
			)
			if friend_item is None:
				return
			resolved_gid = current_friend_uid_or_emit(ctx, "chatmsg", friend_item)
			if resolved_gid is None:
				return
			gid = resolved_gid
			# `name or "-"` 只挡假值：对象 / 数组 / 布尔会原样进每条消息的 from
			# 与 TTY 标题（Issue #2748）。
			friend_name = friend_display_name(friend_item)
			# securityId 是每请求轮换的令牌，必须用本次 friend_list 返回的新值，
			# 否则会拿一个已失效的令牌去请求消息历史。
			resolved_security_id = current_friend_security_id_or_emit(ctx, "chatmsg", friend_item)
			if resolved_security_id is None:
				return
			fresh_security_id = resolved_security_id

		try:
			resp = platform.chat_history(gid, fresh_security_id, page=page, count=count)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "chatmsg", exc, fallback_message="当前平台不支持聊天记录能力")
			return
		if not platform.is_success(resp):
			handle_platform_error_output(ctx, "chatmsg", platform, resp, fallback_message="聊天记录获取失败")
			return
		msg_data = platform.unwrap_data(resp) or {}
		messages = message_list(msg_data)
		pagination = list_pagination(msg_data, page=page, rows=messages)

		result = []
		for msg in messages:
			result.append(_normalize_message(msg, gid=gid, friend_name=friend_name, include_raw=show_raw))

		def _render(data: list[dict[str, Any]]) -> None:
			render_simple_list(
				data,
				f"聊天记录 — {friend_name}",
				[
					("发送方", "from", "bold cyan"),
					("类型", "type", "dim"),
					("内容", "text", "yellow"),
					("时间", "time", "dim"),
				],
			)

		hints: dict[str, Any] = {"next_actions": [
			boss_command_for_ctx(ctx, "chat", include_data_dir=True),
			boss_command_for_ctx(ctx, f"detail {positional_hint(fresh_security_id)}", include_data_dir=True),
		]}
		if pagination["has_more"] is True and page_can_advance(pagination):
			# 翻页命令必须保留 --raw：只写 --page / --count 时下一页的
			# type_code / timestamp / body / links / security_id 全部消失，
			# Agent 会以为保真字段只在第一页存在。
			if direct_uid:
				# 直读路径的翻页要带 --friend-uid 与同一 security_id，否则重放会
				# 退回全列表扫描（Issue #2277）。位置参数放在选项之后，值以 ``-``
				# 开头时由 positional_hint 前置 ``--``。
				next_page = (
					f"chatmsg --friend-uid {shell_argument(gid)} "
					f"--page {pagination['page'] + 1} --count {count}"
				)
				if show_raw:
					next_page += " --raw"
				next_page += f" {positional_hint(fresh_security_id)}"
			else:
				# 非直读路径的 uid 来自沟通列表，仍是可执行提示的位置参数：
				# 选项在前、uid 经 positional_hint 放末尾，前导 '-' 才能被 '--' 保护。
				next_page = f"chatmsg --page {pagination['page'] + 1} --count {count}"
				if show_raw:
					next_page += " --raw"
				next_page += f" {positional_hint(gid)}"
			hints["next_actions"].append(boss_command_for_ctx(ctx, next_page, include_data_dir=True))
		elif pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]

		handle_output(
			ctx, "chatmsg", result,
			pagination=pagination,
			render=_render,
			hints=hints,
		)
