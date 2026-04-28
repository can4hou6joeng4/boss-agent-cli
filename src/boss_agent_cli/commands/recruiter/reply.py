"""招聘者 — 回复候选人消息。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("reply")
@click.argument("friend_id", type=int)
@click.argument("message")
@click.pass_context
@handle_auth_errors("recruiter-reply")
def reply_cmd(ctx: click.Context, friend_id: int, message: str) -> None:
	"""回复候选人消息（friend_id 可从 chat/applications 获取）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.send_message(friend_id, message)
		if not platform.is_success(result):
			code, error_message = platform.parse_error(result)
			handle_error_output(
				ctx, "recruiter-reply",
				code=code,
				message=error_message or "消息发送失败",
				recoverable=False,
			)
			return
		data = {
			"friend_id": friend_id,
			"message": message,
			"sent": True,
		}
		handle_output(
			ctx, "recruiter-reply", data,
			hints={"next_actions": [
				"boss hr chat — 查看沟通列表",
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历",
			]},
		)
