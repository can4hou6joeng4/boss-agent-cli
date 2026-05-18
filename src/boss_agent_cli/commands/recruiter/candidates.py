"""招聘者 — 候选人搜索。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output


@click.command("candidates")
@click.argument("query", required=False, default="")
@click.option("--city", default=None, help="城市筛选（cityCode，如 101020100；-2=全国）")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--experience", default=None, help="经验要求，如 -3,-3（应届）/ -1,-1（不限）")
@click.option("--degree", default=None, help="学历要求，如 201,201 / -1,-1")
@click.option("--age", default=None, help="年龄范围，如 20,25")
@click.option("--school-level", default=None, help="学校层次（如 1101）")
@click.option("--activeness", default=None, help="活跃度，如 2")
@click.option("--source", default=None, help="来源编码（默认 4）")
@click.option("--salary", default=None, help="薪资范围，如 -1,3")
@click.option("--select", is_flag=True, default=False, help="是否带 select=true")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recruiter-candidates")
def candidates_cmd(ctx: click.Context, query: str, city: str | None, job_id: str | None, experience: str | None, degree: str | None, age: str | None, school_level: str | None, activeness: str | None, source: str | None, salary: str | None, select: bool, page: int) -> None:
	"""搜索候选人"""
	if not require_compliance_allowed(ctx, "recruiter-candidates"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.search_geeks(
			query, city=city, page=page, job_id=job_id,
			experience=experience, degree=degree,
			age=age, school_level=school_level,
			activeness=activeness, source=source,
			select=select, salary=salary,
		)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-candidates",
				code=code,
				message=message or "候选人搜索失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
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
