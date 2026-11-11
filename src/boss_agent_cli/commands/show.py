import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.services.job_card import build_job_from_card
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_not_supported, handle_output, handle_platform_error_output, render_job_detail
from boss_agent_cli.index_cache import get_index_info, get_job_by_index


@click.command("show")
@click.argument("index", type=int)
@click.pass_context
@handle_auth_errors("show")
def show_cmd(ctx: click.Context, index: int) -> None:
	"""按编号查看搜索/推荐结果中的职位详情（如 boss show 3）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	search_cmd = boss_command_for_ctx(ctx, "search <关键词>")
	recommend_cmd = boss_command_for_ctx(ctx, "recommend")
	show_cmd = boss_command_for_ctx(ctx, "show <编号>")
	rerun_hints = {"next_actions": [search_cmd, recommend_cmd]}

	# 索引是 1-based 的编号：0 或负数永远不可能命中，先按参数错误拒绝，
	# 不要让「先 search 再 show」的提示把 Agent 送进一个必然失败的循环。
	if index < 1:
		handle_error_output(
			ctx,
			"show",
			code="INVALID_PARAM",
			message=f"编号必须从 1 开始，收到 {index}",
			recovery_action=f"按 1 到结果数之间的编号重试，例如 {show_cmd}",
			hints=rerun_hints,
		)
		return

	# 编号只对当前索引快照有效：后一次 search / recommend 会整份覆盖缓存，
	# 同一个「结果 #3」可能已经指向别的职位。先取快照元信息，成功输出也要带上
	# 来源与保存时间，Agent 才能发现自己手里的编号已经过期。
	info = get_index_info(data_dir)
	if not info["exists"]:
		handle_error_output(
			ctx, "show",
			code="INVALID_PARAM",
			message="没有缓存的搜索结果，请先执行 boss search 或 boss recommend",
			recovery_action=f"先执行 {search_cmd} 或 {recommend_cmd} 建立结果缓存",
			hints=rerun_hints,
		)
		return

	# 从索引缓存获取职位信息
	job = get_job_by_index(data_dir, index)
	if job is None:
		handle_error_output(
			ctx, "show",
			code="INVALID_PARAM",
			message=f"编号 {index} 超出范围，当前缓存共 {info['count']} 条结果（来源: {info['source']}）",
			recovery_action=f"改用当前结果数以内的编号，或用 {search_cmd} 重新搜索",
			hints=rerun_hints,
		)
		return

	security_id = job.get("security_id", "")
	lid = job.get("lid") or ""
	if not security_id:
		handle_error_output(
			ctx, "show",
			code="INVALID_PARAM",
			message=f"编号 {index} 的职位缺少 security_id",
			recovery_action=f"这条缓存缺少平台标识；用 {search_cmd} 重新搜索后再查看",
			hints=rerun_hints,
		)
		return

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
	with get_platform_instance(ctx, auth) as platform:
		try:
			raw = platform.job_card(security_id, lid)
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
			# 职位没了不等于线索断了：Agent 要能直接换关键词重搜，而不是停在
			# 「下架」上（与索引越界那条提示同一组下一步）。
			hints={"next_actions": [boss_command_for_ctx(ctx, "search <关键词>"), boss_command_for_ctx(ctx, "recommend")]},
		)
		return

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		greeted = cache.is_greeted(security_id, job_id=str(job.get("job_id") or ""), account=account)

	result = build_job_from_card(card, security_id=security_id, greeted=greeted, lid=lid)
	result["index"] = index
	# 与 index 一起返回快照身份：source 形如 search:<关键词> 或 recommend，
	# page 是建立快照的页码（旧缓存或未知时为 None），saved_at 是 Unix 秒。
	# 编号离开这份快照就没有意义，重跑搜索后必须重新定位。
	result["index_snapshot"] = {
		"source": info["source"],
		"page": info.get("page"),
		"count": info["count"],
		"saved_at": info["saved_at"],
	}

	# TTY 的 next 行可以写「A 或 B」，但 hints.next_actions 的每条都必须能直接执行。
	next_action = (
		f"{boss_command_for_ctx(ctx, 'apply <security_id> <job_id>')} 或 "
		f"{boss_command_for_ctx(ctx, 'greet <security_id> <job_id>')}"
	)
	hints = {
		"next_actions": [
			boss_command_for_ctx(ctx, "apply <security_id> <job_id>"),
			boss_command_for_ctx(ctx, "greet <security_id> <job_id>"),
			boss_command_for_ctx(ctx, "search <query>"),
		],
	}
	handle_output(
		ctx,
		"show",
		result,
		render=lambda data: render_job_detail(data, greet_command=next_action),
		hints=hints,
	)
