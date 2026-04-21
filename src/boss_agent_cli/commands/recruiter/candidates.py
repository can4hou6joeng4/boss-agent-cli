"""招聘者 — 候选人池。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_output


@click.command("candidates")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recruiter-candidates")
def candidates_cmd(ctx: click.Context, page: int) -> None:
	"""查看候选人池"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.list_applications(page=page)
		data = result.get("zpData", {})
		handle_output(
			ctx, "recruiter-candidates", data,
			hints={"next_actions": [
				"boss --role recruiter recruiter resume <id> — 查看简历",
				"boss --role recruiter recruiter chat — 查看沟通",
			]},
		)
