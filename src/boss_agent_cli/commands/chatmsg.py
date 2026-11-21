import re
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._contact_lookup import current_friend_security_id_or_emit, current_friend_uid_or_emit, resolve_friend_or_emit
from boss_agent_cli.commands._platform import get_platform_instance
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
from boss_agent_cli.services.chat_utils import int_code, message_list, safe_local_time
from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION, list_pagination, page_can_advance

_MSG_TYPE_MAP = {
	1: "文本", 2: "图片", 3: "招呼", 4: "简历", 5: "系统",
	6: "名片", 7: "语音", 8: "视频", 9: "表情",
}


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
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.option("--count", default=20, type=click.IntRange(min=1), help="每页消息数量")
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
			friend_name = friend_item.get("name") or "-"
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
			boss_command_for_ctx(ctx, f"detail {fresh_security_id}", include_data_dir=True),
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
				next_page = f"chatmsg {gid} --page {pagination['page'] + 1} --count {count}"
				if show_raw:
					next_page += " --raw"
			hints["next_actions"].append(boss_command_for_ctx(ctx, next_page, include_data_dir=True))
		elif pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]

		handle_output(
			ctx, "chatmsg", result,
			pagination=pagination,
			render=_render,
			hints=hints,
		)


def _normalize_message(
	msg: dict[str, Any],
	*,
	gid: str,
	friend_name: str,
	include_raw: bool = False,
) -> dict[str, Any]:
	from_obj = msg.get("from", {})
	sender_uid = ""
	sender_name: Any = None
	if isinstance(from_obj, dict):
		sender_uid = str(from_obj.get("uid") or "").strip()
		sender_name = from_obj.get("name")
	# 发送方 uid 缺失（含 null）时无法判断是谁：旧实现用 ``"" != gid`` 把
	# 这类消息误标成「我」（Issue #1352）。缺失按「未知」处理，只有明确的
	# 非空 uid 才能判定发送方。
	if not sender_uid:
		from_name = "未知"
	elif sender_uid == gid:
		from_name = sender_name or friend_name
	else:
		from_name = "我"

	msg_time = ""
	ts = msg.get("time")
	# 平台偶尔把时间戳写成字符串 / 对象：只接受真正的数字，坏值按「无时间」输出。
	if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts:
		msg_dt = safe_local_time(ts)
		# 越界 / 非有限时间戳是确定性坏数据，按「无时间」处理（Issue #1295）。
		msg_time = msg_dt.strftime("%m-%d %H:%M") if msg_dt is not None else ""

	body = _message_body(msg)
	text = msg.get("text") or body.get("text", "") or ""
	msg_type = msg.get("type")
	normalized: dict[str, Any] = {
		"from": from_name,
		"type": _message_type_label(msg_type),
		"text": text,
		"time": msg_time,
	}
	if include_raw:
		normalized.update({
			"message_id": msg.get("id") or msg.get("msgId") or msg.get("messageId"),
			"type_code": msg.get("type"),
			"timestamp": msg.get("time"),
			"body": body,
			"links": _extract_links(msg, body=body),
			"security_id": _first_present(msg, body, ("securityId", "security_id")),
			"job_id": _first_present(msg, body, ("jobId", "job_id", "encryptJobId")),
			"raw": msg,
		})
	return normalized


def _message_body(msg: dict[str, Any]) -> dict[str, Any]:
	body = msg.get("body")
	if isinstance(body, dict):
		return body
	return {}


def _message_type_label(msg_type: Any) -> str:
	# JSON 布尔不是平台消息类型编码：True 不能显示成「文本」（Issue #915）。
	code = int_code(msg_type)
	if code is not None:
		return _MSG_TYPE_MAP.get(code, f"其他({code})")
	return f"其他({msg_type})"


def _first_present(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...]) -> Any:
	for source in (primary, secondary):
		for key in keys:
			value = source.get(key)
			if value not in (None, ""):
				return value
	return None


def _extract_links(msg: dict[str, Any], *, body: dict[str, Any]) -> list[str]:
	links: list[str] = []
	for value in list(msg.values()) + list(body.values()):
		if isinstance(value, str):
			links.extend(re.findall(r"https?://[^\s\"'<>]+", value))
		elif isinstance(value, dict):
			links.extend(_extract_links(value, body={}))
		elif isinstance(value, list):
			for item in value:
				if isinstance(item, str):
					links.extend(re.findall(r"https?://[^\s\"'<>]+", item))
				elif isinstance(item, dict):
					links.extend(_extract_links(item, body={}))
	return list(dict.fromkeys(links))
