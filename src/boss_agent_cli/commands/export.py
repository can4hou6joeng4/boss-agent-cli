from typing import Any

import click

from boss_agent_cli.api.models import dict_items, JobItem
from boss_agent_cli.auth.identity import account_scope
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.crawler.service import CrawlBudget
from boss_agent_cli.output import write_document_stdout
from boss_agent_cli.display import (
	boss_command_for_ctx,
	console,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	json_forced,
	platform_error_contract,
	platform_error_message,
	read_only_platform_code,
	render_export_summary,
	render_job_table,
)
from boss_agent_cli.services.job_identity import deduplicate_job_result
from boss_agent_cli.services.job_export import (
	export_is_truncated,
	export_stop_reason,
	prepare_export_items,
	public_html_export_item_from_api,
	public_html_export_item_from_job,
	redact_export_item,
	render_html_export,
	truncation_operator_action,
	write_export_file,
	write_html_export,
)
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.pagination import DEFAULT_EXPORT_COUNT, export_page_budget
from boss_agent_cli.search_filters import (
	ACTIVE_LEVELS,
	SearchFilterCriteria,
	SearchPipelinePlatformError,
	SearchUrlParseError,
	active_filter_hint,
	detail_channel_for,
	detail_filter_max_pages,
	ensure_supported_city,
	ensure_text_encodable,
	parse_boss_search_url,
	prefilter_platform_job_type,
	resolve_active_level,
	resolve_search_code_params,
	resolve_welfare_keywords,
	run_search_pipeline,
)
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.commands._output_path import validate_output_path_or_emit


def _truncation_note(stop_reason: str) -> str:
	"""截断说明与向导导出面板共用 services/job_export 的唯一文案（Issue #3054）。"""
	return truncation_operator_action(stop_reason)


#: 管线终止原因 → export 既有 stop_reason 词汇（Issue #2706）。
#: 映射与截断判定都在 services/job_export.py，向导 export 共用同一份（Issue #2712）。


def _welfare_conditions(raw: str | None) -> list[tuple[str, list[str]]] | None:
	"""把 `--welfare` 的逗号分隔标签转成管线用条件；无标签返回 None。"""
	if not raw:
		return None
	labels = [label.strip() for label in raw.split(",") if label.strip()]
	if not labels:
		return None
	return [(label, resolve_welfare_keywords(label)) for label in labels]


def _collect_with_detail_filters(
	ctx: click.Context,
	platform: Any,
	data_dir: Any,
	logger: Any,
	criteria: SearchFilterCriteria,
	*,
	count: int,
	welfare_conditions: list[tuple[str, list[str]]] | None,
	active: str | None,
) -> tuple[list[dict[str, Any]] | None, str, Any]:
	"""`--welfare` / `--active` 导出走与 search / 向导相同的搜索管线。

	福利与活跃度都要先看详情才能判断，不能沿用列表页的轻量循环：详情探测、
	间隔预算、风控停止与未知文案排除必须复用 ``run_search_pipeline``，
	否则同一组筛选在 export / search / 向导之间结果不一致（Issue #2706）。

	返回 ``(items, stop_reason, pipeline_result)``；平台错误已按 export 信封
	发出时 items 为 None。
	"""
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	account = account_scope(auth)
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		request_budget = CrawlBudget(cache) if ctx.obj.get("platform", "zhipin") == "zhipin" else None
		detail_channel = detail_channel_for(ctx.obj)
		extra: dict[str, Any] = {}
		if request_budget is not None:
			extra["before_list_request"] = lambda: request_budget.wait("list")
		if active or detail_channel == "browser":
			if request_budget is not None:
				extra["before_detail_request"] = lambda: request_budget.wait("list")
			extra["detail_channel"] = detail_channel
		try:
			result = run_search_pipeline(
				platform,
				cache,
				logger,
				criteria=criteria,
				start_page=1,
				# 福利 / 活跃度要逐个查详情：页数预算不能超过搜索的详情上限，
				# 否则导出会比 search 多翻几倍详情页、放大风控风险。
				max_pages=min(export_page_budget(count), detail_filter_max_pages(welfare_conditions, active)),
				limit=count,
				welfare_conditions=welfare_conditions,
				active=active,
				account=account,
				continue_when_has_more_unknown=True,
				**extra,
			)
		except SearchPipelinePlatformError as exc:
			# 只读命令：未识别响应可按网络问题重试；风控 / 登录过期按登记表。
			code = read_only_platform_code("export", exc.code)
			recoverable, recovery_action, error_hints = platform_error_contract(code, ctx=ctx, read_only=True)
			handle_error_output(
				ctx,
				"export",
				code=code,
				message=platform_error_message(code, exc.message, "搜索结果获取失败"),
				recoverable=recoverable,
				recovery_action=recovery_action,
				details=exc.details,
				hints=error_hints,
			)
			return None, "", None
	return result.items, export_stop_reason(result.stop_reason), result


def _hint_command(ctx: click.Context, command: str) -> str:
	"""导出失败后的重试与后继命令沿用当前登录态与数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


@click.command("export")
@click.argument("query", required=False)
@click.option("--url", "search_url", default=None, help="BOSS 直聘搜索页 URL（可从网页复制完整筛选条件）")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--experience", default=None, help="经验要求，支持逗号分隔多选")
@click.option("--education", default=None, help="学历要求，支持逗号分隔多选")
@click.option("--industry", default=None, help="行业类型，支持逗号分隔多选")
@click.option("--scale", default=None, help="公司规模，支持逗号分隔多选")
@click.option("--stage", default=None, help="融资阶段，支持逗号分隔多选")
@click.option("--job-type", default=None, help="职位类型，支持逗号分隔多选")
@click.option("--welfare", default=None, help="福利筛选，如 五险一金,双休；需要逐个查详情，更慢、风险更高")
@click.option("--active", default=None, help="只导出 HR 活跃度不低于该档的职位：" + " / ".join(ACTIVE_LEVELS) + "（也认 3day、3日 等写法）")
@click.option("--count", default=DEFAULT_EXPORT_COUNT, type=click.IntRange(min=1), help="导出数量")
@click.option("--format", "fmt", default="csv", type=click.Choice(["html", "csv", "json"]), help="输出格式")
@click.option("--output", "-o", default=None, help="输出文件路径（csv/json 不指定时输出到 stdout JSON 信封；html 不指定时直出 HTML，--json 时放进 data.content；显式空串无效）")
@click.option(
	"--include-private",
	is_flag=True,
	help="CSV/JSON/stdout 保留明文平台标识和招聘者姓名；HTML 省略平台标识、招聘者和薪资",
)
@click.pass_context
@handle_auth_errors("export")
def export_cmd(
	ctx: click.Context,
	query: str | None,
	search_url: str | None,
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
	count: int,
	fmt: str,
	output: str | None,
	include_private: bool,
) -> None:
	"""导出搜索结果为 HTML / CSV / JSON 文件"""
	# 与 search 同一口径：传了 --url 但值是空串时，「未提供 query」会读成
	# 「你没传 --url」，真正的原因（flag 后面少了值）被掩盖。
	if search_url is not None and not search_url.strip():
		export_example = _hint_command(ctx, "export <关键词>")
		handle_error_output(
			ctx,
			"export",
			code="INVALID_PARAM",
			message="--url 的值不能为空",
			recovery_action=f"{export_example} 或 {_hint_command(ctx, 'export --url <搜索页URL>')}",
			hints={"next_actions": [export_example]},
		)
		return
	# 与 search 同一口径：纯空白的 query 仍是「没给关键词」，不能当成一次
	# 合法的全量导出——那会翻页抓取大量无关职位，并写出一份内容可疑的文件。
	if query is not None and not query.strip():
		query = None
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	raw_params: dict[str, str] = {}

	if search_url:
		try:
			parsed_url = parse_boss_search_url(search_url)
		except SearchUrlParseError as exc:
			export_cmd = _hint_command(ctx, "export <关键词>")
			handle_error_output(
				ctx,
				"export",
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action=f"换成完整的 BOSS 直聘搜索页链接，或改用关键词重试，例如 {export_cmd}",
				hints={"next_actions": [export_cmd]},
			)
			return
		query = query or parsed_url.query
		raw_params.update(parsed_url.params)

	if not query and not search_url:
		handle_error_output(
			ctx,
			"export",
			code="INVALID_PARAM",
			message="未提供 query，请传入搜索关键词或 --url",
			recoverable=False,
			recovery_action=(
				f"{_hint_command(ctx, 'export <query>')} 或 {_hint_command(ctx, 'export --url <搜索页URL>')}"
			),
		)
		return
	query = query or ""

	# 与 search 同口径：关键词原样进入 httpx 的 URL 参数，孤立代理项会在组
	# 参数时抛 UnicodeEncodeError，被兜成带磁盘指引的 LOCAL_IO_ERROR
	# （Issue #2528）。
	try:
		ensure_text_encodable("搜索关键词", query)
	except ValueError as exc:
		emit_filter_error(ctx, "export", exc)
		return

	try:
		ensure_supported_city(city)
	except ValueError as exc:
		emit_filter_error(ctx, "export", exc)
		return

	try:
		raw_params.update(
			resolve_search_code_params(
				salary=salary,
				experience=experience,
				education=education,
				industry=industry,
				scale=scale,
				stage=stage,
				job_type=job_type,
			)
		)
	except ValueError as exc:
		emit_filter_error(ctx, "export", exc)
		return
	try:
		# 福利 / 活跃度与 search 同口径：标签解析与档位归一化都在任何平台请求前完成。
		welfare_conditions = _welfare_conditions(welfare)
		active_level = resolve_active_level(active)
	except ValueError as exc:
		emit_filter_error(ctx, "export", exc)
		return
	criteria = SearchFilterCriteria(
		query=query,
		city=city,
		salary=salary,
		experience=experience,
		education=education,
		industry=industry,
		scale=scale,
		stage=stage,
		job_type=job_type,
		raw_params=raw_params,
	)

	# 参数错误必须在第一次平台请求之前暴露：`-o` 指向目录时，抓完 5 页再报路径
	# 错误既消耗请求预算、也可能触发风控，而真正该做的只是换个文件名。
	if output is not None:
		# 显式空串不是「输出到默认位置」的写法：旧的真值判断把它化装成「没传」，
		# `--format html -o ""` 直接向 stdout 输出裸 HTML，csv/json 则静默不写
		# 文件（Issue #1501）。必须在任何平台请求前拒绝。
		if not output.strip():
			export_example = _hint_command(ctx, "export <关键词> -o <文件路径>")
			handle_error_output(
				ctx,
				"export",
				code="INVALID_PARAM",
				message=(
					"-o / --output 不能为空；省略 -o 才会输出到默认位置"
					"（--format html 直接写 stdout，其余格式进 JSON 信封）"
				),
				recovery_action=f"给出完整文件路径后重试，例如 {export_example}",
				hints={"next_actions": [export_example]},
			)
			return
		validated = validate_output_path_or_emit(ctx, "export", output)
		if validated is None:
			return
		output = validated

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		all_items: list[dict[str, Any]] = []
		html_items: list[dict[str, Any]] = []
		html_mode = fmt == "html"
		collected = html_items if html_mode else all_items
		seen_job_ids: set[str] = set()
		page = 1
		# 本地严格筛选可能剔除整页结果，额外扫描固定页数；上限仍由用户
		# 显式请求的 count 决定，避免 hasMore 异常时形成无界请求。
		max_pages = export_page_budget(count)
		# 预算耗尽而仍未取满 count 时结果并不完整。必须把终止原因写进信封，
		# 否则调用方无法区分「平台只有这些」和「本地扫描到上限」（Issue #907）。
		stop_reason = "page_budget_exhausted"
		active_hints: dict[str, Any] | None = None

		if welfare_conditions or active_level:
			pipeline_items, stop_reason, pipeline_result = _collect_with_detail_filters(
				ctx,
				platform,
				data_dir,
				logger,
				criteria,
				count=count,
				welfare_conditions=welfare_conditions,
				active=active_level,
			)
			if pipeline_items is None:
				return
			if active_level:
				active_hints = active_filter_hint(active_level, pipeline_result)
			if html_mode:
				html_items.extend(public_html_export_item_from_job(item) for item in pipeline_items)
			else:
				all_items.extend(pipeline_items)

		# 详情筛选路径已经由管线一次取满；保留原列表循环作为无筛选导出的快路径。
		while not (welfare_conditions or active_level) and len(collected) < count and page <= max_pages:
			logger.info(f"正在获取第 {page} 页...")
			search_filters: dict[str, Any] = {"page": page}
			for key, value in {
				"city": city,
				"salary": salary,
				"experience": experience,
				"education": education,
				"industry": industry,
				"scale": scale,
				"stage": stage,
				"job_type": job_type,
			}.items():
				if value:
					search_filters[key] = value
			if raw_params:
				search_filters["raw_params"] = raw_params
			raw = platform.search_jobs(query, **search_filters)
			if not platform.is_success(raw):
				# 搜索结果失败同样走登记表：登录过期可重新登录，风控码带回停止指引。
				handle_platform_error_output(
					ctx,
					"export",
					platform,
					raw,
					fallback_message="搜索结果获取失败",
				)
				return
			unwrapped = platform.unwrap_data(raw)
			platform_data = unwrapped if isinstance(unwrapped, dict) else {}
			raw_job_list = platform_data.get("jobList")
			job_list = dict_items(raw_job_list)
			if not job_list and isinstance(raw_job_list, list) and not raw_job_list:
				# 平台明确给出空列表才算列表结束。非空容器里元素全是 null /
				# 数字 / 字符串时不能提前终止，否则 hasMore=true 的后续页会被
				# 静默丢掉（Issue #2932）；缺容器同理。
				stop_reason = "empty_page"
				break

			for raw_item in deduplicate_job_result(job_list, seen_job_ids=seen_job_ids):
				if len(collected) >= count:
					break
				matches, _ = prefilter_platform_job_type(raw_item, criteria, platform_name=platform.name)
				if not matches:
					continue
				if html_mode:
					html_items.append(public_html_export_item_from_api(raw_item))
				else:
					item = JobItem.from_api(raw_item)
					all_items.append(item.to_dict())

			# 只有平台明确返回布尔 false 才算「没有下一页」。缺失或类型不明确的
			# hasMore 被当成 false 会让 export 静默只导出第一页（Issue #903）；
			# 与 crawler 的保守默认一致，继续翻页，仍受 count / max_pages /
			# 明确空 jobList 三重终止约束；整页元素漂移不是终止条件（Issue #2932）。
			if platform_data.get("hasMore") is False:
				stop_reason = "platform_has_more_false"
				break
			page += 1

		if len(collected) >= count:
			stop_reason = "count_reached"
		# 页数预算耗尽，或活跃度筛选按设计提前停止且没取满 --count：两者都不完整。
		# `active_target_reached` 若恰好取满 count 已被上面的 count_reached 覆盖。
		# 判定下沉到 services/job_export.py，向导 export 共用同一份（Issue #2712）。
		truncated = export_is_truncated(stop_reason, item_count=len(collected), requested=count)

		if output is not None:
			output = str(prepare_output_path(output))
			if html_mode:
				write_html_export(html_items, output)
				item_count = len(html_items)
			else:
				write_items = prepare_export_items(all_items, include_private=include_private)
				write_export_file(write_items, fmt, output)
				item_count = len(all_items)
			data = {
				"message": f"已导出 {item_count} 条到 {output}",
				"count": item_count,
				"format": fmt,
				"path": output,
				"private_fields": _private_fields_state(fmt=fmt, include_private=include_private),
				"truncated": truncated,
				"stop_reason": stop_reason,
			}
			handle_output(
				ctx,
				"export",
				data,
				render=lambda d: render_export_summary(d),
				hints=_with_active_hints(
					_truncation_hints(
						truncated,
						stop_reason=stop_reason,
						next_actions=[
							_hint_command(ctx, "search <query>"),
							_hint_command(ctx, "recommend"),
						],
					),
					active_hints,
				),
			)
		elif html_mode:
			# `--format html` 不能静默退化成职位记录数组：格式契约说 html，就该是 HTML。
			html_text = render_html_export(html_items)
			if json_forced(ctx):
				# 与 digest / stats 一致：--json 下 stdout 只放信封，正文进 data.content。
				handle_output(
					ctx,
					"export",
					{
						"format": "html",
						"content": html_text,
						"bytes": len(html_text.encode("utf-8")),
						"count": len(html_items),
						"private_fields": "omitted",
						"truncated": truncated,
						"stop_reason": stop_reason,
					},
					hints=_with_active_hints(
						_truncation_hints(
							truncated,
							stop_reason=stop_reason,
							next_actions=[_hint_command(ctx, "export <query> --format html -o <path>")],
							operator_actions=["把 data.content 写成 .html 文件后打开"],
						),
						active_hints,
					),
				)
			else:
				# stdout 直出 HTML（便于 `boss export <query> --format html > jobs.html`）
				write_document_stdout(html_text)
				if truncated:
					# 直出模式没有信封，截断证据只能走 stderr，不能污染 stdout。
					console.print(f"[yellow]注意：{_truncation_note(stop_reason)}[/yellow]")
				if active_hints and active_hints.get("unknown_excluded"):
					console.print(
						f"[yellow]注意：{active_hints['unknown_excluded']} 个职位的活跃度无法识别，已按 --active 筛选排除。[/yellow]"
					)
		else:
			write_items = all_items if include_private else [redact_export_item(item) for item in all_items]
			data = {
				"count": len(all_items),
				"format": fmt,
				"jobs": write_items,
				"truncated": truncated,
				"stop_reason": stop_reason,
			}
			handle_output(
				ctx,
				"export",
				data,
				render=lambda d: render_job_table(d.get("jobs", []), "export"),
				hints=_with_active_hints(
					_truncation_hints(
						truncated,
						stop_reason=stop_reason,
						next_actions=[_hint_command(ctx, "export <query> -o file.csv")],
					),
					active_hints,
				),
			)


def _with_active_hints(hints: dict[str, Any], active_hints: dict[str, Any] | None) -> dict[str, Any]:
	"""把活跃度筛选证据并入 export 的 hints（与 search 的 hints.active_filter 同键）。"""
	if active_hints:
		hints = {**hints, "active_filter": active_hints}
	return hints


def _truncation_hints(
	truncated: bool,
	*,
	stop_reason: str = "",
	next_actions: list[str] | None = None,
	operator_actions: list[str] | None = None,
) -> dict[str, list[str]]:
	"""导出终止原因的后继提示：页数预算耗尽与活跃度提前停止都算不完整。"""
	actions = list(operator_actions or [])
	if truncated:
		actions.append(_truncation_note(stop_reason))
	hints: dict[str, list[str]] = {}
	if next_actions:
		hints["next_actions"] = next_actions
	if actions:
		hints["operator_actions"] = actions
	return hints


def _private_fields_state(*, fmt: str, include_private: bool) -> str:
	if fmt == "html":
		return "omitted"
	return "included" if include_private else "redacted"
