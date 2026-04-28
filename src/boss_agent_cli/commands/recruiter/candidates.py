"""招聘者 — 候选人搜索。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output


@click.command("candidates")
@click.argument("query", required=False, default="")
@click.option("--city", default=None, help="城市筛选")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--experience", default=None, help="经验要求")
@click.option("--degree", default=None, help="学历要求")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recruiter-candidates")
def candidates_cmd(ctx: click.Context, query: str, city: str | None, job_id: str | None, experience: str | None, degree: str | None, page: int) -> None:
	"""搜索候选人"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.search_geeks(
			query, city=city, page=page, job_id=job_id,
			experience=experience, degree=degree,
		)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			handle_error_output(
				ctx, "recruiter-candidates",
				code=code,
				message=message or "候选人搜索失败",
				recoverable=False,
			)
			return
		data = platform.unwrap_data(result) or {}
		handle_output(
			ctx, "recruiter-candidates", data,
			hints={"next_actions": [
				"boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历",
				"boss hr chat — 查看沟通",
			]},
		)
