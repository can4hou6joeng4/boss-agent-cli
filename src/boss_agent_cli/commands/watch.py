import click
from typing import Any

from boss_agent_cli.api.endpoints import (
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import (
	CacheRecordInvalid,
	CacheStore,
	SavedSearchParamsError,
	saved_search_name_error,
)
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import account_scope, get_platform_instance
from boss_agent_cli.display import (
	RISK_ERROR_CONTRACTS,
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	platform_error_contract,
	platform_error_message,
	read_only_platform_code,
	render_action_result,
	render_job_table,
	render_list_result,
	render_message_panel,
	positional_hint,
)
from boss_agent_cli.crawler.service import CrawlBudget
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchPipelinePlatformError,
	build_search_params,
	detail_channel_for,
	detail_filter_max_pages,
	ensure_supported_city,
	ensure_text_encodable,
	resolve_search_code_params,
	resolve_welfare_keywords,
	run_search_pipeline,
)
from boss_agent_cli.services.saved_searches import validate_stored_search_params
from boss_agent_cli.commands._filter_errors import emit_filter_error


def _parse_watch_filters(
	query: str,
	city: str | None,
	salary: str | None,
	experience: str | None,
	education: str | None,
	industry: str | None,
	scale: str | None,
	stage: str | None,
	job_type: str | None,
	welfare: str | None,
) -> tuple[dict[str, str | None], list[tuple[str, list[str]]] | None]:
	params = build_search_params(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	welfare_conditions = None
	if welfare:
		labels = [w.strip() for w in welfare.split(",") if w.strip()]
		welfare_conditions = [(label, resolve_welfare_keywords(label)) for label in labels]
	return params, welfare_conditions


@click.group("watch")
def watch_group() -> None:
	"""保存搜索条件并执行增量监控。"""


def _hint_command(ctx: click.Context, command: str) -> str:
	"""watch 的后继命令必须保留原数据目录与平台。

	watch 记录存放在 ``<data_dir>/cache/boss_agent.db``。用自定义 ``--data-dir``
	时，硬编码的 ``boss watch ...`` 会让 Agent 在默认目录里查找、删除或重建
	另一份记录：刚创建的 watch 立刻 ``SAVED_SEARCH_NOT_FOUND``，坏记录也永远
	删不到（Issue #913）。
	"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@watch_group.command("add")
@click.argument("name")
@click.argument("query")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--experience", default=None, help="经验要求")
@click.option("--education", default=None, help="学历要求")
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), metavar="INDUSTRY", help="行业类型（可选值见 boss schema）")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型")
@click.option("--welfare", default=None, help="福利筛选")
@click.pass_context
def watch_add_cmd(
	ctx: click.Context,
	name: str,
	query: str,
	city: str | None,
	salary: str | None,
	experience: str | None,
	education: str | None,
	industry: str | None,
	scale: str | None,
	stage: str | None,
	job_type: str | None,
	welfare: str | None,
) -> None:
	"""保存一条可复用的搜索条件（名称 + 关键词 + 筛选），之后用 run 增量拉取。"""
	# 空字符串是合法 argv，但存下来的记录既没法点名（`boss watch run ` 缺参数），
	# 也没法搜到目标（空关键词等于不筛选）。必须在写盘前拒绝。
	if not name.strip() or not query.strip():
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message="watch 名称与搜索关键词不能为空",
			recovery_action=f"补上 watch 名称和搜索关键词后重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		return
	if problem := saved_search_name_error(name):
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message=f"watch {problem}",
			recovery_action=f"换一个只含普通 Unicode 文本的名称重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		return
	# 保存时就把筛选条件校验掉：watch run 直接把存下来的参数交给平台客户端，
	# 未知薪资 / 经验 / 学历在 `*_CODES.get(...)` 处返回 None，参数被静默丢弃，
	# 之后每次 run 都在「没有筛选」的结果上报告新职位。
	try:
		# 关键词原样进入 httpx 的 URL 参数：孤立代理项必须在写库前拒绝，
		# 否则每次 run 都落成带磁盘指引的 LOCAL_IO_ERROR（Issue #2528）。
		ensure_text_encodable("搜索关键词", query)
		ensure_supported_city(city)
		resolve_search_code_params(
			salary=salary, experience=experience, education=education,
			industry=industry, scale=scale, stage=stage, job_type=job_type,
		)
	except ValueError as exc:
		emit_filter_error(ctx, "watch", exc)
		return

	params, _ = _parse_watch_filters(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	# 与读取侧（watch run）共用同一份存量校验：入口此前只校验关键词的可编码性，
	# `--welfare "\udcff"` 之类能保存成功，随后每次 run 都以 SAVED_SEARCH_INVALID
	# 拒绝该记录，删除后按同样参数重建仍然失败（Issue #2543）。
	try:
		validate_stored_search_params(params)
	except ValueError as exc:
		emit_filter_error(ctx, "watch", exc)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)
	run_cmd = _hint_command(ctx, f"watch run {positional_hint(name)}")
	list_cmd = _hint_command(ctx, "watch list")
	handle_output(
		ctx, "watch",
		{"action": "add", "name": name, "params": params},
		render=lambda d: render_action_result(
			d, title="watch", next_steps=[run_cmd, list_cmd]
		),
		hints={"next_actions": [run_cmd, list_cmd]},
	)


@watch_group.command("list")
@click.pass_context
def watch_list_cmd(ctx: click.Context) -> None:
	"""列出本地已保存的 watch，并提示下一步可执行命令。"""
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items, corrupt = cache.list_saved_searches_with_corrupt()
	add_cmd = _hint_command(ctx, "watch add <name> <关键词>")
	run_cmd = _hint_command(ctx, "watch run <name>")
	remove_cmd = _hint_command(ctx, "watch remove <name>")
	hints: dict[str, Any] = {
		# 空列表没有任何 name 可 run / remove：照抄只会换回一个不存在的 watch。
		"next_actions": [add_cmd] if not items else [run_cmd, remove_cmd]
	}
	if corrupt:
		hints["corrupt"] = corrupt
		hints["operator_actions"] = [
			f"以下条目的参数已损坏，先执行 {_hint_command(ctx, 'watch remove <名称>')} 再重新 watch add：{', '.join(corrupt)}"
		]
	handle_output(
		ctx,
		"watch",
		items,
		render=lambda d: render_list_result(
			d,
			"watches",
			[("名称", "name", "bold cyan"), ("条件", "params", "green"), ("创建时间", "created_at", "dim")],
			next_steps=[add_cmd] if not d else [run_cmd, remove_cmd],
		),
		hints=hints,
	)


@watch_group.command("remove")
@click.argument("name")
@click.pass_context
def watch_remove_cmd(ctx: click.Context, name: str) -> None:
	"""删除一条本地 watch；名字不存在时保持幂等（不报错）。"""
	# 与 add / run 同口径：空名字不可能命中任何本地记录，却会以
	# `{"removed": false}` 的 `ok: true` 报成功，Agent 会以为已经删掉了。
	if not name.strip():
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message="watch 名称不能为空",
			recovery_action=f"补上 watch 名称后重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		return
	if problem := saved_search_name_error(name):
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message=f"watch {problem}",
			recovery_action=f"换一个只含普通 Unicode 文本的名称重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.delete_saved_search(name)
	handle_output(
		ctx,
		"watch",
		{"action": "remove", "name": name, "removed": removed},
		render=lambda d: render_action_result(
			d, title="watch", next_steps=[_hint_command(ctx, "watch list")]
		),
		hints={"next_actions": [_hint_command(ctx, "watch list")]},
	)


def _execute_single_watch(ctx: click.Context, cache: Any, name: str) -> dict[str, Any] | None:
	"""执行单个 watch，返回聚合结果；找不到时返回 None。"""
	try:
		record = cache.get_saved_search(name)
	except CacheRecordInvalid as exc:
		# 时间戳等列类型漂移与参数写坏同口径：本地记录损坏是确定性失败，
		# 不能兜成可重试的 NETWORK_ERROR（Issue #2011）。
		raise SavedSearchParamsError(name, f"本地记录损坏：{exc}") from exc
	if record is None:
		return None

	params = record["params"]
	# 存量记录可能由旧版本或 wizard 写入：运行前按 watch add 同口径重新校验。
	# 关键词必填、参数类型、城市 / 标签 / 活跃度取值域与向导共用同一份判定
	# （Issue #1338、#1798、#2511）。
	try:
		validate_stored_search_params(params)
	except ValueError as exc:
		raise SavedSearchParamsError(name, str(exc)) from exc
	welfare = params.get("welfare")
	_, welfare_conditions = _parse_watch_filters(
		params.get("query"),
		params.get("city"),
		params.get("salary"),
		params.get("experience"),
		params.get("education"),
		params.get("industry"),
		params.get("scale"),
		params.get("stage"),
		params.get("job_type"),
		welfare,
	)
	criteria = SearchFilterCriteria(
		query=params.get("query", ""),
		city=params.get("city"),
		salary=params.get("salary"),
		experience=params.get("experience"),
		education=params.get("education"),
		industry=params.get("industry"),
		scale=params.get("scale"),
		stage=params.get("stage"),
		job_type=params.get("job_type"),
	)
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
	active = params.get("active")
	extra: dict[str, Any] = {}
	detail_channel = detail_channel_for(ctx.obj)
	if active or detail_channel == "browser":
		# 活跃度要查详情或 CDP 模式时串行 + 间隔，CDP 下只走浏览器 job_card（#451 回归修复）
		if ctx.obj.get("platform", "zhipin") == "zhipin":
			budget = CrawlBudget(cache)
			extra["before_detail_request"] = lambda: budget.wait("list")
		extra["detail_channel"] = detail_channel
	with get_platform_instance(ctx, auth) as platform:
		pipeline_result = run_search_pipeline(
			platform,
			cache,
			logger,
			criteria=criteria,
			start_page=1,
			max_pages=detail_filter_max_pages(welfare_conditions, active),
			welfare_conditions=welfare_conditions,
			active=active,
			account=account,
			**extra,
		)
	watch_result = cache.record_watch_results(name, pipeline_result.items, account=account)
	return {
		"name": name,
		"new_count": watch_result["new_count"],
		"seen_count": watch_result["seen_count"],
		"total_count": watch_result["total_count"],
		"new_items": watch_result["new_items"],
	}


def _render_watch_run(data: dict[str, Any]) -> None:
	"""单个 watch 的终端结果：面板只放计数，新增职位用表格展示。

	``new_items`` 是完整搜索结果项的列表，直接插进面板会被渲染成一大段 dict
	repr——一次几十条新职位时既撑爆面板，也没法扫读标题和薪资。
	"""
	items = data.get("new_items")
	summary = {key: value for key, value in data.items() if key != "new_items"}
	render_message_panel(summary, title="watch run")
	if isinstance(items, list) and items:
		render_job_table(items, "新增职位")


@watch_group.command("run")
@click.argument("name", required=False)
@click.option("--all", "run_all", is_flag=True, help="跑所有已保存 watch")
@click.pass_context
@handle_auth_errors("watch")
def watch_run_cmd(ctx: click.Context, name: str | None, run_all: bool) -> None:
	"""执行一条或全部 watch，把新增职位写入本地缓存。"""
	if not require_compliance_allowed(ctx, "watch-run"):
		ctx.exit(1)

	# 点名与 --all 是两种运行范围：同时给出时旧实现进入 --all 分支，静默丢弃
	# 点名的 watch 并运行全部，对每个 watch 都发起平台请求却仍返回 ok:true
	# （Issue #1561）。必须在打开缓存与发出任何请求之前拒绝。
	if name is not None and not name.strip():
		# 纯空白名不可能命中任何本地记录，NOT_FOUND 的重建提示又会被 watch add
		# 的空白校验拒绝，Agent 会陷入 run → NOT_FOUND → add → INVALID_PARAM
		# 的死循环（Issue #1800）。与 remove 同口径在入口拒绝。
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message="watch 名称不能为空",
			recovery_action=f"补上 watch 名称后重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		ctx.exit(1)
		return

	if name is not None and (problem := saved_search_name_error(name)):
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message=f"watch {problem}",
			recovery_action=f"换一个只含普通 Unicode 文本的名称重试；用 {list_cmd} 查看已有 watch",
			hints={"next_actions": [list_cmd]},
		)
		ctx.exit(1)
		return

	if run_all and name:
		list_cmd = _hint_command(ctx, "watch list")
		handle_error_output(
			ctx,
			"watch",
			code="INVALID_PARAM",
			message=f"--all 不能与 watch 名同时使用：{name!r} 会被忽略并运行全部 watch",
			recoverable=False,
			recovery_action="只点名运行一个 watch（省略 --all），或去掉名字改用 --all 运行全部",
			hints={"next_actions": [list_cmd, _hint_command(ctx, "watch run --all")]},
		)
		return

	data_dir = ctx.obj["data_dir"]

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		if run_all:
			# 参数损坏的条目不能静默跳过：--all 返回 ok: true 却少跑一个 watch，
			# 用户看不出任何异常。与 `watch list` 一样单独列出，并给出删除重建指引。
			records, corrupt = cache.list_saved_searches_with_corrupt()
			watches: list[dict[str, Any]] = []
			risk_error: SearchPipelinePlatformError | None = None
			stopped_watch: str | None = None
			for record in records:
				try:
					summary = _execute_single_watch(ctx, cache, record["name"])
				except SavedSearchParamsError as exc:
					# 单条存量参数失效不能中止整批，也不能静默跳过：记录进
					# watches 并给出删除重建指引，其余 watch 继续执行（Issue #1338）。
					summary = {
						"name": record["name"],
						"error": "SAVED_SEARCH_INVALID",
						"message": str(exc),
					}
				except SearchPipelinePlatformError as exc:
					if exc.code in RISK_ERROR_CONTRACTS:
						# 风控是整条 workflow 的硬边界：停止后续 watch，
						# 不再向平台发起请求；已完成的部分随 details 带回。
						risk_error = exc
						stopped_watch = record["name"]
						break
					summary = {"name": record["name"], "error": exc.code, "message": exc.message}
				if summary is None:
					# 记录在列出之后按名称读取不到：并发删除会让它自然消失。
					# 静默丢弃会让 --all 报 ok: true 却少跑一个 watch，必须显式
					# 列出并说明原因（Issue #2164 同批：此前 BLOB 名称的记录
					# 也会落进这个分支且完全不可见）。
					summary = {
						"name": record["name"],
						"error": "SAVED_SEARCH_INVALID",
						"message": "watch 记录在运行前已不存在（可能被并发删除）；请用 watch list 确认后重新创建",
					}
				watches.append(summary)
			if risk_error is not None:
				recoverable, recovery_action, risk_hints = platform_error_contract(risk_error.code, ctx=ctx)
				handle_error_output(
					ctx, "watch",
					code=risk_error.code,
					message=platform_error_message(risk_error.code, risk_error.message, "搜索触发平台风控"),
					recoverable=recoverable,
					recovery_action=recovery_action,
					details={"watches": watches, "stopped_watch": stopped_watch},
					hints=risk_hints,
				)
				return
			all_hints: dict[str, Any] = {
				# 一个 watch 都没跑成时没有任何 security_id 可 detail：照抄只会换回一个
				# 必然失败的编号，下一步应该是先建一个 watch。
				"next_actions": [_hint_command(ctx, "watch add <name> <关键词>")]
				if not watches
				else [
					_hint_command(ctx, "watch list"),
					_hint_command(ctx, "detail <security_id>"),
				]
			}
			if corrupt:
				all_hints["corrupt"] = corrupt
				all_hints["operator_actions"] = [
					f"以下 watch 的参数已损坏、本次未执行，先执行 {_hint_command(ctx, 'watch remove <名称>')} 再重新 watch add：{', '.join(corrupt)}"
				]
			invalid = [
				item["name"]
				for item in watches
				if item.get("error") == "SAVED_SEARCH_INVALID" and item.get("name")
			]
			if invalid:
				all_hints.setdefault("operator_actions", []).append(
					"以下 watch 的筛选条件已失效、本次未执行，"
					f"先执行 {_hint_command(ctx, 'watch remove <名称>')} 再重新 watch add：{', '.join(invalid)}"
				)
			handle_output(
				ctx, "watch",
				{"mode": "all", "watches": watches, "total": len(watches), "skipped": corrupt, "invalid": invalid},
				render=lambda d: render_message_panel(
					{"模式": "全部", "监控数": d.get("total", 0)}, title="watch run"
				),
				hints=all_hints,
			)
			return

		if not name:
			list_cmd = _hint_command(ctx, "watch list")
			handle_error_output(
				ctx, "watch",
				code="INVALID_PARAM",
				message="必须传入 watch 名或使用 --all",
				recoverable=False,
				recovery_action=f"用 {list_cmd} 查看已保存的 watch 后点名运行，或加 --all 运行全部",
				hints={"next_actions": [list_cmd, _hint_command(ctx, "watch run --all")]},
			)
			return

		try:
			summary = _execute_single_watch(ctx, cache, name)
		except SavedSearchParamsError as exc:
			# 参数写坏的是本地记录：给删除重建指引，不能兜成 NETWORK_ERROR 让它重试。
			handle_error_output(
				ctx,
				"watch",
				code="SAVED_SEARCH_INVALID",
				message=str(exc),
				hints={"next_actions": [
					_hint_command(ctx, f"watch remove {positional_hint(name)}"),
					_hint_command(ctx, f"watch add {positional_hint(name)} <关键词>"),
				]},
			)
			return
		except SearchPipelinePlatformError as exc:
			# 与 search 一致：风控码给停止指引，登录过期等可恢复错误给恢复动作。
			# watch 只读：未识别响应按 NETWORK_ERROR 允许安全重试（#1519）。
			error_code = read_only_platform_code("watch", exc.code)
			recoverable, recovery_action, hints = platform_error_contract(
				error_code, ctx=ctx, read_only=True
			)
			handle_error_output(
				ctx,
				"watch",
				code=error_code,
				message=platform_error_message(error_code, exc.message, "搜索结果获取失败"),
				recoverable=recoverable,
				recovery_action=recovery_action,
				details=exc.details,
				hints=hints,
			)
			return

		if summary is None:
			handle_error_output(
				ctx, "watch", code="SAVED_SEARCH_NOT_FOUND", message=f"未找到 watch: {name}",
				recovery_action=f"用 {_hint_command(ctx, 'watch list')} 查看已保存的 watch 后重试",
				hints={"next_actions": [
					_hint_command(ctx, "watch list"),
					_hint_command(ctx, f"watch add {positional_hint(name)} <关键词>"),
				]},
			)
			return

		handle_output(
			ctx, "watch", summary,
			render=_render_watch_run,
			hints={"next_actions": [
				_hint_command(ctx, "detail <security_id>"),
				_hint_command(ctx, "watch list"),
			]},
		)
