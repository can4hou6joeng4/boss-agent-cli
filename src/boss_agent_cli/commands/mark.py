import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._contact_lookup import current_friend_uid_or_emit, resolve_friend_or_emit
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_message_panel
from boss_agent_cli.services.labels import (
	LABEL_MAP as _LABEL_MAP,
	LABEL_NAMES as _LABEL_NAMES,
	brief_label as _brief_label,
)


def _resolve_label(label_input: str) -> int:
	"""将标签名称或 ID 解析为数字 ID。"""
	# 空串是任何标签名的子串，模糊匹配会把它解析成「新招呼」并真的在平台上
	# 添加 / 移除标签（Issue #1376）；纯空白同样不可能是有效标签。
	if not label_input.strip():
		raise click.BadParameter(
			f"标签不能为空。可用: {', '.join(_LABEL_MAP.keys())}"
		)
	if label_input in _LABEL_MAP:
		return _LABEL_MAP[label_input]
	# 全角 / 上标数字（'①'、'²'）的 `isdigit()` 为真但 `int()` 会抛 ValueError，
	# 落成参数错误以外的内部错误；只接受 ASCII 数字串（Issue #1306）。
	# 长度上限：4300 位以上纯数字串让 int() 抛裸 ValueError（int_max_str_digits），
	# 被顶层兜成可重试的 NETWORK_ERROR（Issue #1437）。标签 ID 是远端小整数。
	if label_input.isascii() and label_input.isdigit():
		try:
			label_id = int(label_input)
		except ValueError:
			raise click.BadParameter(
				f"标签 ID 无效: {_brief_label(label_input)}。可用: {', '.join(_LABEL_MAP.keys())}"
			) from None
		# 数字 ID 与标签名一一对应（Issue #1575）：999 之类的任意数字既不是
		# 已知标签，也不能原样发给平台标签接口，否则会在平台上创建一个
		# 无名标签或直接失败。
		if label_id in _LABEL_NAMES:
			return label_id
		raise click.BadParameter(
			f"未知标签 ID: {_brief_label(label_input)}。可用数字 ID: "
			f"{', '.join(str(v) for v in sorted(_LABEL_MAP.values()))}"
		)
	# 部分子串（如「招呼」「面」）过去会模糊命中标签并发起真实写操作；MCP enum
	# 与 CLI help 都只承诺精确名称 / 数字 ID，这里同步收口（Issue #1615）。
	raise click.BadParameter(
		f"未知标签: {_brief_label(label_input)}。可用: {', '.join(_LABEL_MAP.keys())}；"
		f"数字 ID: {', '.join(str(v) for v in sorted(_LABEL_MAP.values()))}"
	)


@click.command("mark")
@click.argument("security_id", metavar="UID_OR_SECURITY_ID")
@click.option("--label", required=True, help="标签名称（新招呼/沟通中/已约面/已获取简历/已交换电话/已交换微信/不合适/牛人发起/收藏）或数字 ID")
@click.option("--remove", is_flag=True, default=False, help="移除标签（默认为添加）")
@click.pass_context
@handle_auth_errors("mark")
def mark_cmd(ctx: click.Context, security_id: str, label: str, remove: bool) -> None:
	"""给联系人添加/移除标签"""
	if not require_compliance_allowed(ctx, "mark"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	label_id = _resolve_label(label)
	label_name = _LABEL_NAMES.get(label_id, str(label_id))
	action_text = "移除" if remove else "添加"

	with get_platform_instance(ctx, auth) as platform:
		friend_item = resolve_friend_or_emit(ctx, "mark", platform, security_id)
		if friend_item is None:
			return
		friend_id = current_friend_uid_or_emit(ctx, "mark", friend_item)
		if friend_id is None:
			return
		friend_source = friend_item.get("friendSource", 0)
		# friendSource 只是回传给标签接口的定位参数：平台字段漂移成非数字串 /
		# 对象 / 1e400 时按缺失的默认 0 处理，与向导侧同口径（Issue #1314、#1439）。
		# 原值直接进 httpx params 会发出一次注定失败的写请求（消耗预算、可能触发风控）。
		try:
			friend_source = int(friend_source or 0)
		except (TypeError, ValueError, OverflowError):
			friend_source = 0
		friend_name = friend_item.get("name") or "-"

		try:
			resp = platform.friend_label(friend_id, label_id, friend_source, remove=remove)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "mark", exc, fallback_message="当前平台不支持联系人标签能力")
			return
		if not platform.is_success(resp):
			handle_platform_error_output(ctx, "mark", platform, resp, fallback_message=f"{action_text}标签失败")
			return

		# 只认字符串并与 _contact_lookup 同口径：类型漂移成对象 / 布尔时
		# str() 会把 Python repr 当 security_id 回显，Agent 会拿它继续调用
		# detail / apply 等命令（Issue #1812、#2138）。
		raw_security_id = friend_item.get("securityId") or friend_item.get("security_id")
		data = {
			"uid": friend_id,
			"security_id": raw_security_id.strip() if isinstance(raw_security_id, str) else "",
			"name": friend_name,
			"label": label_name,
			"action": action_text,
			"message": f"已{action_text}标签「{label_name}」",
		}
		handle_output(
			ctx, "mark", data,
			render=lambda d: render_message_panel(d, title="mark"),
			hints={"next_actions": [
				boss_command_for_ctx(ctx, "chat", include_data_dir=True),
			]},
		)
