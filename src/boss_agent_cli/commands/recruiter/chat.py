"""招聘者 — 候选人沟通。"""
from typing import Any

import click

from boss_agent_cli.api.request_budget import is_request_stop
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.services.chat_utils import message_list
from boss_agent_cli.services.recruiter_chat_summary import (
	clean_unread_by_tab,
	enrich_friend_summaries,
	friend_ids_from_items,
	friend_items,
	normalize_last_message,
	summaries_by_friend,
	unread_totals,
)
from boss_agent_cli.services.recruiter_last_messages import (
	DEFAULT_SUMMARY_LIMIT,
	MAX_SUMMARY_LIMIT,
	fetch_last_messages,
	friend_sources,
	list_dict_items,
)
from boss_agent_cli.services.pagination import (
	UNKNOWN_HAS_MORE_OPERATOR_ACTION,
	list_pagination,
	page_can_advance,
)
from boss_agent_cli.commands.recruiter._hints import hint_command, reject_blank_options
from boss_agent_cli.commands.recruiter._render import (
	render_friend_list,
	render_last_messages,
	render_message_history,
)
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_output,
	handle_platform_error_output,
	shell_argument,
)


def _message_cursor_id(value: Any) -> int | None:
	"""消息 ``mid`` 是正整数或数字字符串；其他形状不能当游标。"""
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value if value > 0 else None
	if isinstance(value, str):
		# 平台字段漂移成 '①' / '²' 时 `isdigit()` 为真但 `int()` 会抛
		# ValueError，让整个聊天历史命令丢掉信封；只认 ASCII 数字串（Issue #1306）。
		text = value.strip()
		if text.isascii() and text.isdigit():
			try:
				cursor = int(text)
			except (ValueError, OverflowError):
				# 超过 4300 位的 ASCII 数字串仍会触发 int_max_str_digits 上限；
				# 漂移字段不能让整条命令落成可重试的网络错误（Issue #1317）。
				return None
			return cursor if cursor > 0 else None
	return None


def _chat_history_pagination(data: Any) -> dict[str, Any]:
	"""聊天历史用游标分页：复用三态 ``hasMore``，下一页游标取本页最旧的 ``mid``。

	平台把 ``hasMore`` 放在响应顶层；页码字段对 ``--max-msg-id`` 不适用，因此
	这里不返回 ``page``。
	"""
	messages = message_list(data)
	pagination = list_pagination(data, page=1, rows=messages)
	pagination.pop("page", None)
	mids = [
		mid
		for msg in messages
		if isinstance(msg, dict) and (mid := _message_cursor_id(msg.get("mid"))) is not None
	]
	pagination["next_max_msg_id"] = min(mids) if mids else None
	return pagination


def _friend_data(data: Any) -> dict[str, Any]:
	if isinstance(data, dict):
		return data
	if isinstance(data, list):
		return {"friendList": list_dict_items(data)}
	return {}


def _fetch_friend_ids(
	platform: Any, *, page: int, label_id: int, job_id: str | None,
) -> tuple[list[int], dict[int, int], dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
	"""取一页沟通列表，返回 ids / 来源 / 错误 / 平台载荷 / 实际提取出的联系人行。

	载荷供调用方推导 ``page`` / ``hasMore``；行数供 ``total``，两者必须来自同一次
	提取，否则 ``result`` / ``list`` 形态会出现「有联系人、total 为 null」（Issue #2900）。
	"""
	result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
	if not platform.is_success(result):
		return [], {}, result, {}, []
	data = _friend_data(platform.unwrap_data(result))
	items = friend_items(data)
	ids = friend_ids_from_items(items)
	return ids, friend_sources(items), None, data, items


def _last_messages_page(
	ctx: Any, pagination: dict[str, Any], job_id: str | None, label_id: int, limit: int,
) -> str:
	"""下一页沟通列表命令：保留筛选条件，非默认 limit 才透传。"""
	next_page = f"hr last-messages --page {pagination['page'] + 1}"
	if job_id:
		next_page += f" --job-id {shell_argument(job_id)}"
	if label_id:
		next_page += f" --label-id {label_id}"
	if limit != DEFAULT_SUMMARY_LIMIT:
		next_page += f" --limit {limit}"
	return hint_command(ctx, next_page)


def _page_summaries(platform: Any) -> tuple[dict[int, dict[str, Any]], dict[str, Any] | None]:
	"""CDP 模式下读聊天页已加载的会话列表（只读页面内存，不发请求）；拿不到返回空。

	返回 (按 friendId 的摘要, 快照元信息)；元信息含数据来源路径、已加载条数、页面是否还有更多。
	"""
	snapshot_fn = getattr(platform, "chat_list_snapshot", None)
	if not callable(snapshot_fn):
		return {}, None
	try:
		snapshot = snapshot_fn()
	except Exception as exc:  # noqa: BLE001 — 页面快照只是增强，失败退回接口/列表字段
		# 风控 / 预算停止不是“快照不可用”：吞掉后调用方还会继续发 userLastMsg 请求，
		# 违反「命中风控立即停止」的硬边界。页面快照的普通失败仍按增强项降级。
		if is_request_stop(exc):
			raise
		return {}, None
	if not isinstance(snapshot, dict) or not snapshot.get("ok"):
		return {}, None
	summaries = summaries_by_friend(list_dict_items(snapshot.get("items")))
	meta: dict[str, Any] = {"loaded": len(summaries)}
	unread_by_tab = clean_unread_by_tab(snapshot.get("unread_by_tab"))
	if unread_by_tab is not None:
		meta["unread_by_tab"] = unread_by_tab
	if isinstance(snapshot.get("source"), str):
		meta["source"] = snapshot["source"]
	if isinstance(snapshot.get("has_more"), bool):
		meta["has_more"] = snapshot["has_more"]
	return summaries, meta


def _enrich_chat_summaries(
	platform: Any,
	friend_rows: list[dict[str, Any]],
	*,
	summary_limit: int = DEFAULT_SUMMARY_LIMIT,
) -> dict[str, Any]:
	"""给沟通列表补未读数 / 最后一条消息 / 时间，返回来源统计（放进 hints）。

	请求量：页面快照不发请求；页面没覆盖到的会话里只取列表前 summary_limit 个，
	按 50 个一批串行调 userLastMsg（默认最多 2 次请求），其余会话只保留列表自带字段。
	合并与归一化实现与向导 action 共用 services/recruiter_chat_summary。
	"""
	page, page_meta = _page_summaries(platform)
	hint: dict[str, Any] = {}
	if page_meta is not None:
		hint["page_snapshot"] = page_meta
	if not friend_ids_from_items(friend_rows):
		return hint
	hint.update(
		enrich_friend_summaries(platform, friend_rows, summary_limit=summary_limit, page_summaries=page)
	)
	return hint


@click.command("chat")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=click.IntRange(min=0), help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.option(
	"--summary-limit", default=DEFAULT_SUMMARY_LIMIT, show_default=True,
	type=click.IntRange(0, MAX_SUMMARY_LIMIT),
	help="最多给前多少个会话补最近消息（每 50 个一次串行请求；0=不请求）",
)
@click.pass_context
@handle_auth_errors("recruiter-chat")
def recruiter_chat_cmd(ctx: click.Context, page: int, job_id: str | None, label_id: int, summary_limit: int) -> None:
	"""查看与候选人的沟通列表"""
	if not require_compliance_allowed(ctx, "recruiter-chat"):
		return
	if reject_blank_options(ctx, "recruiter-chat", job_id=job_id):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-chat", platform, result, fallback_message="沟通列表获取失败")
			return
		data = _friend_data(platform.unwrap_data(result))
		friend_rows = friend_items(data)
		summary_hint = _enrich_chat_summaries(platform, friend_rows, summary_limit=summary_limit)
		# total 必须按实际提取出的联系人行计数：只认 friendList 会让 result /
		# list 形态的整页记录报成 null（Issue #2900）。
		pagination = list_pagination(data, page=page, rows=friend_rows)
		hints: dict[str, Any] = {"next_actions": [
			hint_command(ctx, "hr resume <geek_id> --job-id <id> --security-id <id>"),
			hint_command(ctx, "hr chatmsg <friend_id>"),
		]}
		if pagination["has_more"] is True and page_can_advance(pagination):
			next_page = f"hr chat --page {pagination['page'] + 1}"
			if job_id:
				next_page += f" --job-id {shell_argument(job_id)}"
			if label_id:
				next_page += f" --label-id {label_id}"
			next_page += f" --summary-limit {summary_limit}"
			hints["next_actions"].append(hint_command(ctx, next_page))
		if pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		# 未读总数只读页面内存（uncountTab$），不额外发请求；放在 hints 顶层，拿不到为 null。
		hints.update(unread_totals(summary_hint.get("page_snapshot")))
		if summary_hint:
			hints["summary_sources"] = summary_hint
		handle_output(
			ctx, "recruiter-chat", data,
			render=lambda d: render_friend_list(d, title="hr chat", next_steps=hints["next_actions"]),
			pagination=pagination, hints=hints,
		)


@click.command("chatmsg")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.option("--count", default=20, type=click.IntRange(min=1), help="消息数量")
@click.option("--max-msg-id", default=None, type=click.IntRange(min=1), help="向前翻页的最大消息 ID")
@click.pass_context
@handle_auth_errors("recruiter-chatmsg")
def recruiter_chatmsg_cmd(ctx: click.Context, friend_id: int, count: int, max_msg_id: int | None) -> None:
	"""查看与指定候选人的聊天消息历史"""
	if not require_compliance_allowed(ctx, "recruiter-chatmsg"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.chat_history(friend_id, count=count, max_msg_id=max_msg_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-chatmsg", platform, result, fallback_message="聊天记录获取失败")
			return
		data = platform.unwrap_data(result) or {}
		pagination = _chat_history_pagination(data)
		hints: dict[str, list[str]] = {"next_actions": [
			hint_command(ctx, f"hr reply {friend_id} <message>"),
			hint_command(ctx, "hr chat"),
		]}
		if pagination["has_more"] is True and pagination["next_max_msg_id"] is not None:
			hints["next_actions"].append(hint_command(
				ctx,
				f"hr chatmsg {friend_id} --max-msg-id {pagination['next_max_msg_id']} --count {count}",
			))
		elif pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		handle_output(
			ctx, "recruiter-chatmsg", data,
			render=lambda d: render_message_history(d, title="hr chatmsg", next_steps=hints["next_actions"]),
			pagination=pagination,
			hints=hints,
		)


@click.command("last-messages")
@click.option("--page", default=1, type=click.IntRange(min=1), help="沟通列表页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=click.IntRange(min=0), help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.option("--friend-id", "friend_ids", multiple=True, type=click.IntRange(min=1), help="指定候选人会话 friend_id，可重复")
@click.option(
	"--limit", default=DEFAULT_SUMMARY_LIMIT, show_default=True,
	type=click.IntRange(1, MAX_SUMMARY_LIMIT),
	help="最多查询多少个会话（每 50 个一次串行请求）",
)
@click.pass_context
@handle_auth_errors("recruiter-last-messages")
def recruiter_last_messages_cmd(
	ctx: click.Context,
	page: int,
	job_id: str | None,
	label_id: int,
	friend_ids: tuple[int, ...],
	limit: int,
) -> None:
	"""批量查看候选人最近消息摘要"""
	if not require_compliance_allowed(ctx, "recruiter-last-messages"):
		return
	if reject_blank_options(ctx, "recruiter-last-messages", job_id=job_id):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		ids = list(dict.fromkeys(friend_ids))
		sources: dict[int, int] = {}
		# 只有从平台取了一页沟通列表才有分页证据；--friend-id 直查没有列表页，pagination 为 null。
		pagination: dict[str, Any] | None = None
		if not ids:
			ids, sources, error, page_data, page_rows = _fetch_friend_ids(
				platform, page=page, label_id=label_id, job_id=job_id,
			)
			if error is not None:
				handle_platform_error_output(ctx, "recruiter-last-messages", platform, error, fallback_message="沟通列表获取失败")
				return
			# 与 `hr chat` 同一口径：total 是本次实际提取出的联系人行数（Issue #2900）。
			pagination = list_pagination(page_data, page=page, rows=page_rows)
		if not ids:
			# 空结果不能只回一个空列表：Agent 拿不到任何后继动作，也不知道
			# 是「没有会话」还是「筛选条件太窄」。
			next_actions = [hint_command(ctx, "hr chat")]
			operator_actions = [
				f"没有可读取的候选人会话：先用 {hint_command(ctx, 'hr chat')} 查看沟通列表，"
				f"或用 {hint_command(ctx, 'hr candidates <关键词>')} 主动搜索候选人"
			]
			if pagination is not None:
				if pagination["has_more"] is True and page_can_advance(pagination):
					next_actions.append(_last_messages_page(ctx, pagination, job_id, label_id, limit))
				if pagination["has_more"] is None:
					operator_actions.append(UNKNOWN_HAS_MORE_OPERATOR_ACTION)
			handle_output(
				ctx,
				"recruiter-last-messages",
				{"friend_ids": [], "messages": []},
				render=lambda d: render_last_messages(d, title="hr last-messages", next_steps=next_actions),
				pagination=pagination,
				hints={"next_actions": next_actions, "operator_actions": operator_actions},
			)
			return

		skipped = max(0, len(ids) - limit)
		ids = ids[:limit]
		items, stats = fetch_last_messages(platform, ids, sources=sources)
		if "error" in stats and not items:
			handle_platform_error_output(
				ctx, "recruiter-last-messages", platform,
				stats["failed_response"],
				fallback_message="最近消息获取失败",
			)
			return
		known = set(ids)
		messages = [normalize_last_message(item, known) for item in items]
		output: dict[str, Any] = {"friend_ids": ids, "messages": messages, "requests": stats["requests"]}
		if skipped:
			output["skipped"] = skipped
		if "error" in stats:
			output["partial_error"] = stats["error"]
		next_actions = [
			hint_command(ctx, "hr chatmsg <friend_id>"),
			hint_command(ctx, "hr reply <friend_id> <message>"),
		]
		hints: dict[str, Any] = {"next_actions": next_actions}
		if pagination is not None:
			if pagination["has_more"] is True and page_can_advance(pagination):
				next_actions.append(_last_messages_page(ctx, pagination, job_id, label_id, limit))
			if pagination["has_more"] is None:
				hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		handle_output(
			ctx, "recruiter-last-messages",
			output,
			render=lambda d: render_last_messages(d, title="hr last-messages", next_steps=next_actions),
			pagination=pagination,
			hints=hints,
		)
