"""招聘者 — 候选人搜索。"""
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_output,
	handle_platform_error_output,
	shell_argument,
)
from boss_agent_cli.services.pagination import (
	UNKNOWN_HAS_MORE_OPERATOR_ACTION,
	list_pagination,
	rows_from,
)
from boss_agent_cli.wizard.actions import execute_recruiter_candidate_search


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
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
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
		result = execute_recruiter_candidate_search(platform, {
			"query": query,
			"city": city,
			"page": page,
			"job_id": job_id,
			"experience": experience,
			"degree": degree,
			"age": age,
			"school_level": school_level,
			"activeness": activeness,
			"source": source,
			"select": select,
			"salary": salary,
		})
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-candidates", platform, result, fallback_message="候选人搜索失败")
			return
		data = platform.unwrap_data(result) or {}
		pagination = list_pagination(data, page=page, rows=rows_from(data, "geekList"))
		next_actions = [
			hint_command(ctx, "hr resume <geek_id> --job-id <id> --security-id <id>"),
			hint_command(ctx, "hr chat"),
		]
		if pagination["has_more"] is True:
			# 翻页必须保留本次筛选条件，否则第 2 页与第 1 页不是同一组结果。
			parts = ["hr", "candidates"]
			if query:
				parts.append(shell_argument(query))
			parts.extend(["--page", str(pagination["page"] + 1)])
			for flag, value in (
				("--city", city),
				("--job-id", job_id),
				("--experience", experience),
				("--degree", degree),
				("--age", age),
				("--school-level", school_level),
				("--activeness", activeness),
				("--source", source),
				("--salary", salary),
			):
				if value:
					parts.extend([flag, shell_argument(value)])
			if select:
				parts.append("--select")
			next_actions.append(hint_command(ctx, " ".join(parts)))
		hints: dict[str, Any] = {"next_actions": next_actions}
		if pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		handle_output(
			ctx, "recruiter-candidates", data,
			pagination=pagination,
			hints=hints,
		)
