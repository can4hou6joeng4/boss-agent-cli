import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands.detail import build_job_from_card
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_not_supported, handle_output, handle_platform_error_output, render_job_detail
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
		try:
			raw = platform.job_card(security_id)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "show", exc, fallback_message="当前平台不支持职位详情能力")
			return
		if not platform.is_success(raw):
			handle_platform_error_output(ctx, "show", platform, raw, fallback_message="职位详情获取失败")
			return

	platform_data = platform.unwrap_data(raw) or {}
	card = platform_data.get("jobCard", {})
	if not card:
		handle_error_output(
			ctx, "show",
			code="JOB_NOT_FOUND",
			message="职位不存在或已下架",
		)
		return

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id)

	result = build_job_from_card(card, security_id=security_id, greeted=greeted)
	result["index"] = index

	manual_handoff = "如需投递或沟通，请回到 BOSS 直聘官方页面由用户手动完成"
	hints = {"next_actions": [manual_handoff, "boss search <query>"]}
	handle_output(
		ctx,
		"show",
		result,
		render=lambda data: render_job_detail(data, greet_command=manual_handoff),
		hints=hints,
	)
