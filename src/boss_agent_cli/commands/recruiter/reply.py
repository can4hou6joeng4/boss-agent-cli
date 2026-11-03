"""招聘者 — 回复候选人消息。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
)


@click.command("reply")
@click.argument("friend_id", type=click.IntRange(min=1))
@click.argument("message")
@click.pass_context
@handle_auth_errors("recruiter-reply")
def reply_cmd(ctx: click.Context, friend_id: int, message: str) -> None:
	"""回复候选人消息（friend_id 可从 chat/applications 获取）"""
	if not require_compliance_allowed(ctx, "recruiter-reply"):
		return

	# 空消息会作为一条空白回复真正发到候选人会话里，既不可撤回也没有信息量；
	# 招聘者侧的首次招呼（recommendations.py）早有同样的拦截，这里补齐。
	if not message.strip():
		chat_cmd = boss_command_for_ctx(ctx, "hr chat")
		handle_error_output(
			ctx,
			"recruiter-reply",
			code="INVALID_PARAM",
			message="回复内容不能为空；空白消息会作为一条空消息发送给候选人",
			recovery_action=f"补上回复内容后重试；对话上下文可用 {chat_cmd} 查看",
			hints={"next_actions": [chat_cmd]},
		)
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		# A' 路径：让 BOSS 招聘者前端 Vue 组件代劳真正的 WS 发送 (issue #217)。
		# 旧的 send_message(gid, content) 走 fastReply/sendReplyMsg 已 121 弃端点。
		result = platform.send_message_by_friend(friend_id, message)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-reply", platform, result, fallback_message="消息发送失败")
			return
		data = {
			"friend_id": friend_id,
			"sent": True,
		}
		handle_output(
			ctx, "recruiter-reply", data,
			hints={"next_actions": [
				boss_command_for_ctx(ctx, "hr chat"),
				boss_command_for_ctx(ctx, "hr resume <geek_id> --job-id <id> --security-id <id>"),
			]},
		)
