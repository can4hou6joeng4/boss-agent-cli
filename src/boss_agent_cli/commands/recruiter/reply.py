"""招聘者 — 回复候选人消息。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_output


@click.command("reply")
@click.argument("friend_id", type=int)
@click.argument("message")
@click.pass_context
@handle_auth_errors("recruiter-reply")
def reply_cmd(ctx: click.Context, friend_id: int, message: str) -> None:
	"""回复候选人消息（friend_id 可从 chat/applications 获取）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.send_message(friend_id, message)
		data = {
			"friend_id": friend_id,
			"message": message,
			"sent": platform.is_success(result),
		}
		handle_output(
			ctx, "recruiter-reply", data,
			hints={"next_actions": [
				"boss hr chat — 查看沟通列表",
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历",
			]},
		)
