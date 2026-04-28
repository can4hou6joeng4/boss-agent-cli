"""招聘者 — 请求候选人附件简历。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("request-resume")
@click.argument("friend_id", type=int)
@click.option("--job-id", type=int, required=True, help="关联职位 ID（从 chat/applications 获取）")
@click.pass_context
@handle_auth_errors("recruiter-request-resume")
def request_resume_cmd(ctx: click.Context, friend_id: int, job_id: int) -> None:
	"""请求候选人分享附件简历"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		# friend_id 就是 uid，gid 也设为 uid
		result = platform.exchange_request(3, friend_id, job_id, friend_id)
		if not platform.is_success(result):
			code, error_message = platform.parse_error(result)
			handle_error_output(
				ctx, "recruiter-request-resume",
				code=code,
				message=error_message or "附件简历请求失败",
				recoverable=False,
			)
			return
		data = {
			"friend_id": friend_id,
			"job_id": job_id,
			"requested": True,
			"message": "附件简历请求已发送",
		}
		handle_output(
			ctx, "recruiter-request-resume", data,
			hints={"next_actions": [
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历",
			]},
		)
