import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_output, render_job_detail
from boss_agent_cli.index_cache import get_index_info, get_job_by_index


@click.command("show")
@click.argument("index", type=int)
@click.pass_context
@handle_auth_errors("show")
def show_cmd(ctx: click.Context, index: int) -> None:
	"""按编号查看搜索/推荐结果中的职位详情（如 boss show 3）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	# 从索引缓存获取职位信息
	job = get_job_by_index(data_dir, index)
	if job is None:
		info = get_index_info(data_dir)
		if not info["exists"]:
			handle_error_output(
				ctx, "show",
				code="INVALID_PARAM",
				message="没有缓存的搜索结果，请先执行 boss search 或 boss recommend",
			)
		else:
			handle_error_output(
				ctx, "show",
				code="INVALID_PARAM",
				message=f"编号 {index} 超出范围，当前缓存共 {info['count']} 条结果（来源: {info['source']}）",
			)
		return

	security_id = job.get("security_id", "")
	if not security_id:
		handle_error_output(
			ctx, "show",
			code="INVALID_PARAM",
			message=f"编号 {index} 的职位缺少 security_id",
		)
		return

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		raw = platform.job_card(security_id)

	platform_data = platform.unwrap_data(raw) or {}
	card = platform_data.get("jobCard", {})
	if not card:
		handle_error_output(
			ctx, "show",
			code="JOB_NOT_FOUND",
			message="职位不存在或已下架",
		)
		return

	job_id = card.get("encryptJobId", "")

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id)

	result = {
		"job_id": job_id,
		"title": card.get("jobName", ""),
		"company": card.get("brandName", ""),
		"salary": card.get("salaryDesc", ""),
		"city": card.get("cityName", ""),
		"experience": card.get("experienceName", ""),
		"education": card.get("degreeName", ""),
		"description": card.get("postDescription", ""),
		"address": card.get("address", ""),
		"skills": card.get("jobLabels", []),
		"boss_name": card.get("bossName", ""),
		"boss_title": card.get("bossTitle", ""),
		"boss_active": card.get("activeTimeDesc", "离线"),
		"security_id": security_id,
		"greeted": greeted,
		"index": index,
	}

	greet_target = boss_command_for_ctx(ctx, f"greet {security_id} {job_id}")
	search_target = boss_command_for_ctx(ctx, "search <query>")
	hints = {"next_actions": [greet_target, search_target]}
	handle_output(
		ctx,
		"show",
		result,
		render=lambda data: render_job_detail(data, greet_command=greet_target),
		hints=hints,
	)
