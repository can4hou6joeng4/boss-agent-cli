"""招聘者 — 候选人沟通。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("chat")
@click.option("--page", default=1, type=int, help="页码")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=int, help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.pass_context
@handle_auth_errors("recruiter-chat")
def recruiter_chat_cmd(ctx: click.Context, page: int, job_id: str | None, label_id: int) -> None:
	"""查看与候选人的沟通列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			handle_error_output(
				ctx, "recruiter-chat",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=False,
			)
			return
		data = platform.unwrap_data(result) or {}
		handle_output(
			ctx, "recruiter-chat", data,
			hints={"next_actions": [
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历",
			]},
		)
