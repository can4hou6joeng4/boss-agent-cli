import json
from typing import Any, NamedTuple

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore, SavedSearchParamsError
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.crawler.service import CrawlBudget
from boss_agent_cli.display import (
	SearchProgress,
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	is_json_mode,
	platform_error_contract,
	platform_error_message,
	render_job_table,
	shell_argument,
	show_command_for_ctx,
)
from boss_agent_cli.index_cache import get_index_snapshot, try_save_index
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchPipelinePlatformError,
	SearchUrlParseError,
	ACTIVE_LEVELS,
	detail_channel_for,
	detail_filter_max_pages,
	ensure_supported_city,
	parse_boss_search_url,
	resolve_active_level,
	resolve_search_code_params,
	resolve_welfare_keywords,
	run_search_pipeline,
)
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.wizard.actions import execute_candidate_search


# 3：职位条目新增 boss_active_desc 字段，旧缓存不再复用。
# 4：缓存键带上账号作用域，且命中后按当前账号重算 greeted；旧缓存不再复用。
# 5：缓存里的下一页提示开始保留本次筛选条件；旧条目会把翻页带到另一组结果。
# 6：pagination 改为如实区分「平台说 false」与「平台没说」（Issue #887），
#    旧缓存的 has_more 一律是布尔值，复用会让 Agent 继续读到底气十足的 false。
_SEARCH_CACHE_SCHEMA = 6


def _parse_cached_result(raw: str) -> dict[str, Any] | None:
	"""解析搜索缓存；内容损坏或结构不对时按未命中处理。

	缓存写坏（手工改库、磁盘异常）或结构漂移时，`json.loads` 会抛
	`JSONDecodeError`、`result["data"]` 会抛 `KeyError`，两者都会让
	`boss search` 直接 traceback；重新搜索会覆盖同键的坏条目。
	"""
	try:
		parsed = json.loads(raw)
	except json.JSONDecodeError:
		return None
	if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
		return None
	return parsed


def _sort_search_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
	if sort == "score":
		return sorted(items, key=lambda item: item.get("match_score", 0), reverse=True)
	return items


def _refresh_greeted(items: list[dict[str, Any]], cache: CacheStore, account: str) -> list[dict[str, Any]]:
	"""缓存命中后按当前账号重算 ``greeted``。

	缓存写入后本地可能又打过招呼，直接复用旧标记会让 ``greeted`` 一直停留在
	写入时的状态；账号作用域也要求这里用当前账号查询，而不是沿用缓存里的值。
	"""
	for item in items:
		if not isinstance(item, dict):
			continue
		item["greeted"] = cache.is_greeted(
			str(item.get("security_id") or ""),
			job_id=str(item.get("job_id") or ""),
			account=account,
		)
	return items


def _hint_command(ctx: click.Context, command: str) -> str:
	"""search 的后继命令必须保留原数据目录与平台。

	预设、搜索缓存、登录态都在当前数据目录里。提示写成裸的 ``boss ...`` 时，
	使用自定义 ``--data-dir`` 的调用方照抄会落到默认目录：预设「不存在」、
	翻页读另一份缓存、``detail`` / ``greet`` 用另一个账号（Issue #919）。
	"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _more_hint(next_command: str) -> str:
	"""TTY 表尾的翻页提示。

	next_command 已由 ``_build_next_page_command`` 完成筛选条件与 shell 转义，
	这里只贴前缀：TTY 与信封必须是同一条命令，否则真人和 Agent 翻页后拿到
	不同条件的结果集。
	"""
	return f"more: {next_command}"


class _NextPageParams(NamedTuple):
	"""重建下一页命令所需的本次调用参数（筛选与开关）。"""

	query: str
	search_url: str | None
	city: str | None
	salary: str | None
	experience: str | None
	education: str | None
	industry: str | None
	scale: str | None
	stage: str | None
	job_type: str | None
	no_cache: bool
	with_score: bool
	sort_mode: str


def _build_next_page_command(ctx: click.Context, *, page: int, params: _NextPageParams) -> str:
	"""下一页命令：与本次调用同条件，只把 ``--page`` 换成 N+1。

	只写 ``search <query> --page N+1`` 会让 Agent 丢掉本次生效的全部筛选条件
	（城市 / 薪资 / 经验 / 学历 / 行业 / 规模 / 融资阶段 / 职位类型）、``--url``
	里的网页筛选、``--with-score`` / ``--sort score`` 与 ``--no-cache``，翻页后
	拿到的是另一组结果却以为在继续上一页。``--preset`` 展开成显式筛选：预设
	之后被修改不影响本次翻页的语义。

	带 ``--welfare`` / ``--active`` 的搜索不受影响：它们要逐个查详情，分页
	语义不同，调用方本来就不给下一页提示。
	"""
	argv = ["search"]
	if params.query:
		argv.append(params.query)
	if params.search_url:
		argv.extend(["--url", params.search_url])
	for flag, value in (
		("--city", params.city),
		("--salary", params.salary),
		("--experience", params.experience),
		("--education", params.education),
		("--industry", params.industry),
		("--scale", params.scale),
		("--stage", params.stage),
		("--job-type", params.job_type),
	):
		if value:
			argv.extend([flag, str(value)])
	if params.no_cache:
		argv.append("--no-cache")
	if params.with_score:
		argv.append("--with-score")
	if params.sort_mode != "relevance":
		argv.extend(["--sort", params.sort_mode])
	argv.extend(["--page", str(page + 1)])
	# 前缀（平台 / 数据目录）由 _hint_command 负责转义，这里只转义本次调用参数。
	return _hint_command(ctx, " ".join(shell_argument(part) for part in argv))


_UNKNOWN_HAS_MORE_ACTION = "平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有结果"


def _result_hints(
	ctx: click.Context,
	*,
	page: int,
	params: _NextPageParams,
	has_next_page: bool,
	has_more_unknown: bool,
) -> tuple[dict[str, Any], str]:
	"""成功信封的 hints 与下一页命令（没有下一页时为空串）。

	hints 取决于本次调用的上下文（数据目录、平台、``--sort``），所以每次调用
	都重新构造，不从搜索缓存里取：缓存键不含 ``--sort``，把提示和结果一起
	缓存会让命中的调用拿到别次调用的翻页命令（Issue #919）。
	"""
	hints: dict[str, Any] = {
		"next_actions": [
			_hint_command(ctx, "detail <security_id>"),
			_hint_command(ctx, "apply <security_id> <job_id>"),
			_hint_command(ctx, "greet <security_id> <job_id>"),
		],
	}
	next_page_command = ""
	if has_next_page:
		next_page_command = _build_next_page_command(ctx, page=page, params=params)
		hints["next_actions"].append(next_page_command)
	elif has_more_unknown:
		# 与 recommend 同口径：平台没说下一页，不能凭本页数量判断。
		hints["operator_actions"] = [_UNKNOWN_HAS_MORE_ACTION]
	return hints, next_page_command


def _progress_title(criteria: SearchFilterCriteria, welfare_conditions: Any, active: str | None = None) -> str:
	"""进度头部：把本次搜索条件浓缩成一行，让真人知道正在跑什么。"""
	parts = [f"搜索 {criteria.query}"]
	if criteria.city:
		parts.append(str(criteria.city))
	if welfare_conditions:
		parts.append(",".join(label for label, _ in welfare_conditions))
	if active:
		parts.append(f"HR 活跃 ≥ {active}")
	return " · ".join(parts)


def _active_filter_hint(level: str, pipeline_result: Any) -> dict[str, Any]:
	"""把活跃度筛选的统计写进 hints，未知文案单独列出，方便调用方判断是否放宽条件。"""
	stats = pipeline_result.stats
	hint: dict[str, Any] = {
		"level": level,
		"rejected": stats.active_rejected,
		"unknown_excluded": stats.active_unknown,
	}
	unknown = getattr(pipeline_result, "active_unknown_descs", None) or {}
	if unknown:
		hint["unknown_descs"] = dict(unknown)
		hint["note"] = "活跃度文案无法识别或详情未返回的职位已排除"
	detail_requests = getattr(stats, "detail_requests", 0) or 0
	if detail_requests:
		hint["detail_lookups"] = detail_requests
		hint["detail_note"] = (
			f"列表看不出活跃度，逐个查了 {detail_requests} 个职位详情；"
			"非 online 档位更慢、风控风险更高，低风险场景建议用 --active online"
		)
	return hint


@click.command("search")
@click.argument("query", required=False)
@click.option("--url", "search_url", default=None, help="BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）")
@click.option("--preset", default=None, help="预设名称（从 boss preset add 保存）")
@click.option("--city", default=None, help="城市名称（如 北京、上海）")
@click.option("--salary", default=None, help="薪资范围（如 10-20K）")
@click.option("--experience", default=None, help="经验要求（如 3-5年）")
@click.option("--education", default=None, help="学历要求（如 本科）")
@click.option("--industry", default=None, help="行业类型，支持逗号分隔多选")
@click.option("--scale", default=None, help="公司规模（如 100-499人），支持逗号分隔多选")
@click.option("--stage", default=None, help="融资阶段（如 已上市、A轮），支持逗号分隔多选")
@click.option("--job-type", default=None, help="职位类型（全职/兼职/实习），支持逗号分隔多选")
@click.option("--welfare", default=None, help="福利筛选（如 双休、五险一金），会逐个检查职位详情")
@click.option(
	"--active",
	default=None,
	help="只保留 HR 活跃度不低于该档的职位：" + " / ".join(ACTIVE_LEVELS) + "（也认 3day、3日 等写法）；online 只看列表，其余档位列表看不出时会逐个查职位详情（更慢、风险更高）",
)
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.option("--no-cache", is_flag=True, default=False, help="跳过缓存")
@click.option("--with-score", is_flag=True, default=False, help="附加匹配分和原因")
@click.option("--sort", "sort_mode", default="relevance", type=click.Choice(["relevance", "score"]), help="排序方式")
@click.pass_context
@handle_auth_errors("search")
def search_cmd(
	ctx: click.Context,
	query: str | None,
	search_url: str | None,
	preset: str | None,
	city: str | None,
	salary: str | None,
	experience: str | None,
	education: str | None,
	industry: str | None,
	scale: str | None,
	stage: str | None,
	job_type: str | None,
	welfare: str | None,
	active: str | None,
	page: int,
	no_cache: bool,
	with_score: bool,
	sort_mode: str,
) -> None:
	"""按关键词和筛选条件搜索职位列表"""
	# 传了 flag 但值是空串 / 纯空白时，下面的「未提供 query」会回
	# 「请传入搜索关键词、--preset 或 --url」——用户明明传了 --preset，读到的
	# 却是「你没传」。先点明是哪个选项的值空了。
	if preset is not None and not preset.strip():
		handle_error_output(
			ctx,
			"search",
			code="INVALID_PARAM",
			message="--preset 的值不能为空",
			recovery_action=f"用 {_hint_command(ctx, 'preset list')} 查看已保存的预设，或改用搜索关键词",
			hints={"next_actions": [_hint_command(ctx, "preset list")]},
		)
		return
	if search_url is not None and not search_url.strip():
		handle_error_output(
			ctx,
			"search",
			code="INVALID_PARAM",
			message="--url 的值不能为空",
			recovery_action=(
				f"{_hint_command(ctx, 'search <关键词>')} 或 "
				f"{_hint_command(ctx, 'search --url <搜索页URL>')}"
			),
			hints={"next_actions": [_hint_command(ctx, "search <关键词>")]},
		)
		return
	# 纯空白的 query 与空串等价：`boss search " "` 能绕过下面的「未提供 query」
	# 检查，进入一次没有关键词的搜索——既消耗请求预算，也把账号放在更宽的风控面里。
	if query is not None and not query.strip():
		query = None
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	raw_params: dict[str, str] = {}
	# 校验失败的筛选项若完全来自 preset，错误消息要点名是哪条本地预设：
	# 用户这次没传任何筛选参数，只说「未知薪资范围」看不出该改哪里。
	preset_origin = ""
	if preset and not any((city, salary, experience, education, industry, scale, stage, job_type)):
		remove_cmd = _hint_command(ctx, f"preset remove {shell_argument(preset)}")
		preset_origin = f"（来自 preset '{preset}'，可用 {remove_cmd} 删除后重建）"

	if preset:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			try:
				record = cache.get_saved_search(preset)
			except SavedSearchParamsError as exc:
				# 预设参数写坏时给可执行的删除重建指引，而不是把 JSON 解析错误
				# 兜成 NETWORK_ERROR 让 Agent 无限重试。
				handle_error_output(
					ctx, "search", code="SAVED_SEARCH_INVALID", message=str(exc),
					hints={"next_actions": [
						_hint_command(ctx, f"preset remove {shell_argument(preset)}"),
						_hint_command(ctx, f"preset add {shell_argument(preset)} <关键词>"),
					]},
				)
				return
		if record is None:
			list_cmd = _hint_command(ctx, "preset list")
			handle_error_output(
				ctx, "search", code="SAVED_SEARCH_NOT_FOUND", message=f"未找到 preset: {preset}",
				recovery_action=f"用 {list_cmd} 查看已保存的预设后重试",
				hints={"next_actions": [
					list_cmd,
					_hint_command(ctx, f"preset add {shell_argument(preset)} <关键词>"),
				]},
			)
			return
		params = record["params"]
		query = params.get("query") or query
		city = city or params.get("city")
		salary = salary or params.get("salary")
		experience = experience or params.get("experience")
		education = education or params.get("education")
		industry = industry or params.get("industry")
		scale = scale or params.get("scale")
		stage = stage or params.get("stage")
		job_type = job_type or params.get("job_type")
		welfare = welfare or params.get("welfare")
		active = active or params.get("active")

	if search_url:
		try:
			parsed_url = parse_boss_search_url(search_url)
		except SearchUrlParseError as exc:
			search_cmd = _hint_command(ctx, "search <关键词>")
			handle_error_output(
				ctx,
				"search",
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action=f"换成完整的 BOSS 直聘搜索页链接，或改用关键词重试，例如 {search_cmd}",
				hints={"next_actions": [search_cmd]},
			)
			return
		query = query or parsed_url.query
		raw_params.update(parsed_url.params)
		if parsed_url.page is not None and page == 1:
			page = parsed_url.page

	if not query and not search_url:
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"search",
			code="INVALID_PARAM",
			message="未提供 query，请传入搜索关键词、--preset 或 --url",
			recovery_action=f"补上搜索关键词、--preset 或 --url 后重试，例如 {search_cmd}",
			hints={"next_actions": [search_cmd, _hint_command(ctx, "preset list")]},
		)
		return
	query = query or ""

	try:
		ensure_supported_city(city)
	except ValueError as exc:
		emit_filter_error(ctx, "search", exc, preset_origin)
		return

	try:
		code_params = resolve_search_code_params(
			salary=salary,
			experience=experience,
			education=education,
			industry=industry,
			scale=scale,
			stage=stage,
			job_type=job_type,
		)
	except ValueError as exc:
		emit_filter_error(ctx, "search", exc, preset_origin)
		return
	raw_params.update({key: value for key, value in code_params.items() if value})

	try:
		active_level = resolve_active_level(active)
	except ValueError as exc:
		emit_filter_error(ctx, "search", exc, preset_origin)
		return
	# 福利和活跃度都可能要逐个查详情（活跃度也会变），结果不进搜索缓存
	detail_filter = bool(welfare or active_level)

	# 解析福利关键词（支持逗号分隔的多条件组合）
	welfare_conditions = None
	if welfare:
		labels = [w.strip() for w in welfare.split(",") if w.strip()]
		welfare_conditions = [(label, resolve_welfare_keywords(label)) for label in labels]

	criteria = SearchFilterCriteria(
		query=query, city=city, salary=salary,
		experience=experience, education=education,
		industry=industry, scale=scale, stage=stage,
		job_type=job_type,
		raw_params=raw_params,
	)

	next_page_params = _NextPageParams(
		query=query,
		search_url=search_url,
		city=city,
		salary=salary,
		experience=experience,
		education=education,
		industry=industry,
		scale=scale,
		stage=stage,
		job_type=job_type,
		no_cache=no_cache,
		with_score=with_score,
		sort_mode=sort_mode,
	)

	# 账号作用域要在读缓存之前确定：缓存按账号隔离，命中后也要按当前账号重算 greeted。
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		# 有福利 / 活跃度筛选时跳过缓存（需要逐个查详情，活跃度也会变）
		if not detail_filter and not no_cache and not with_score:
			search_params = {
				"cache_schema": _SEARCH_CACHE_SCHEMA,
				"query": query, "city": city, "salary": salary,
				"experience": experience, "education": education,
				"industry": industry, "scale": scale, "stage": stage,
				"job_type": job_type, "url": search_url, "raw_params": raw_params, "page": page,
				"account": account,
			}
			cached = cache.get_search(search_params)
			if cached is not None:
				result = _parse_cached_result(cached)
				if result is not None:
					logger.debug("搜索命中缓存")
					items = _refresh_greeted(result["data"], cache, account)
					items = _sort_search_items(items, sort_mode)
					saved = try_save_index(data_dir, items, source=f"search:{query}", page=page, logger=logger)
					cached_pagination = result.get("pagination")
					if saved and isinstance(cached_pagination, dict):
						snapshot = get_index_snapshot(data_dir)
						if snapshot is not None:
							cached_pagination["index_snapshot"] = snapshot
					# 提示按本次调用重建，不取缓存里的旧值：分页证据来自缓存的
					# pagination，数据目录 / --sort 来自本次调用。
					cached_has_more = cached_pagination.get("has_more") if isinstance(cached_pagination, dict) else None
					cached_hints, _ = _result_hints(
						ctx,
						page=page,
						params=next_page_params,
						has_next_page=cached_has_more is True,
						has_more_unknown=not isinstance(cached_has_more, bool),
					)
					handle_output(
						ctx, "search", items,
						render=lambda data: render_job_table(
							data, f"search: {query}", show_command=show_command_for_ctx(ctx, index_saved=saved),
						),
						pagination=cached_pagination, hints=cached_hints,
					)
					return
				logger.debug("搜索缓存内容不可用，按未命中处理")

		request_budget = CrawlBudget(cache) if ctx.obj.get("platform", "zhipin") == "zhipin" else None
		# CDP 模式下详情（--active / --welfare 兜底）只走浏览器、串行并按 CrawlBudget 间隔
		detail_channel = detail_channel_for(ctx.obj)
		with get_platform_instance(ctx, auth) as platform:
			max_pages = detail_filter_max_pages(welfare_conditions, active_level)
			# TTY 下把管线日志接到 Rich 进度；管道 / --json 仍用原 logger，行为不变。
			progress = None
			pipeline_logger: Any = logger
			if not is_json_mode(ctx):
				progress = SearchProgress(_progress_title(criteria, welfare_conditions, active_level), max_pages=max_pages)
				progress.start()
				pipeline_logger = progress
			# 仅 TTY 下把节流等待秒数喂给进度条；管道 / --json 保持 wait("list") 逐字不变。
			try:
				pipeline_result = execute_candidate_search(
					platform,
					cache,
					pipeline_logger,
					{
						"query": criteria.query,
						"city": criteria.city,
						"salary": criteria.salary,
						"experience": criteria.experience,
						"education": criteria.education,
						"industry": criteria.industry,
						"scale": criteria.scale,
						"stage": criteria.stage,
						"job_type": criteria.job_type,
						"raw_params": criteria.raw_params,
						"page": page,
						"max_pages": max_pages,
						"welfare_conditions": welfare_conditions,
						"active": active_level,
						"before_detail_request": (
							(lambda: request_budget.wait("list", on_wait=progress.waiting))
							if request_budget is not None and progress is not None
							else (lambda: request_budget.wait("list"))
							if request_budget is not None
							else None
						) if active_level or detail_channel == "browser" else None,
						"detail_channel": detail_channel,
						"account": account,
						"before_list_request": (
							(lambda: request_budget.wait("list", on_wait=progress.waiting))
							if request_budget is not None and progress is not None
							else (lambda: request_budget.wait("list"))
							if request_budget is not None
							else None
						),
					},
					pipeline=run_search_pipeline,
				)
			except SearchPipelinePlatformError as exc:
				# 风控码必须走风险契约：不可恢复、不提示重试；登录过期等
				# 可恢复错误按登记表给出 recovery_action，而不是一律 false。
				recoverable, recovery_action, error_hints = platform_error_contract(exc.code)
				handle_error_output(
					ctx, "search",
					code=exc.code,
					message=platform_error_message(exc.code, exc.message, "搜索结果获取失败"),
					recoverable=recoverable,
					recovery_action=recovery_action,
					details=exc.details,
					hints=error_hints,
				)
				return
			finally:
				if progress is not None:
					progress.finish()
			items = pipeline_result.items
			if with_score:
				items = [score_job_dict(item, criteria=criteria, expect_data=None) for item in items]
			cache_items = items
			output_items = _sort_search_items(items, sort_mode)
			saved = try_save_index(data_dir, output_items, source=f"search:{query}", page=page, logger=logger)

			# Emit search_completed hook
			hooks = ctx.obj.get("hooks")
			if hooks:
				hooks.search_completed.call({
					"query": query,
					"url": search_url,
					"page": page,
					"result_count": len(output_items),
					"stats": {
						"pages_scanned": pipeline_result.stats.pages_scanned,
						"jobs_seen": pipeline_result.stats.jobs_seen,
						"jobs_prefiltered": pipeline_result.stats.jobs_prefiltered,
						"detail_checks": pipeline_result.stats.detail_checks,
					},
					"source": "search",
				})

			# 与 recommend / chat / history / favorites / hr jobs 同一口径：平台
			# 未返回布尔 hasMore 时为 null，不伪造成 false（Issue #887）。
			pagination: dict[str, Any] = {
				"page": page,
				"has_more": pipeline_result.has_more_reported,
				"has_more_source": "platform_hasMore" if pipeline_result.has_more_reported is not None else "unknown",
				"total": pipeline_result.total or len(items),
				"total_kind": "returned_page_count",
			}
			hints, next_page_command = _result_hints(
				ctx,
				page=page,
				params=next_page_params,
				has_next_page=bool(pipeline_result.has_more) and not detail_filter,
				has_more_unknown=pipeline_result.has_more_reported is None and not detail_filter,
			)
			if active_level:
				hints["active_filter"] = _active_filter_hint(active_level, pipeline_result)

			# 缓存普通搜索结果
			if not detail_filter and not with_score:
				search_params = {
					"cache_schema": _SEARCH_CACHE_SCHEMA,
					"query": query, "city": city, "salary": salary,
					"experience": experience, "education": education,
					"industry": industry, "scale": scale, "stage": stage,
					"job_type": job_type, "url": search_url, "raw_params": raw_params, "page": page,
					"account": account,
				}
				# 只缓存结果与分页证据；hints 取决于调用上下文，命中时重建。
				cache_data = {"data": cache_items, "pagination": pagination}
				cache.put_search(search_params, json.dumps(cache_data, ensure_ascii=False))

			# 快照身份只对本次返回有效，不写进搜索缓存；缓存命中时重新读取。
			if saved:
				snapshot = get_index_snapshot(data_dir)
				if snapshot is not None:
					pagination["index_snapshot"] = snapshot

			title_suffix = " (welfare filter)" if welfare_conditions else ""
			if active_level:
				title_suffix += f" (active>={active_level})"
			handle_output(
				ctx, "search", output_items,
				render=lambda data: render_job_table(
					data, f"search: {query}{title_suffix}",
					page=page,
					hint_next=_more_hint(next_page_command) if next_page_command else "",
					show_command=show_command_for_ctx(ctx, index_saved=saved),
				),
				pagination=pagination, hints=hints,
			)
