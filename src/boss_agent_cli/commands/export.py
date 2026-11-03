import sys
from typing import Any

import click

from boss_agent_cli.api.models import JobItem, field_list
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	json_forced,
	render_export_summary,
	render_job_table,
)
from boss_agent_cli.services.job_export import (
	prepare_export_items,
	public_html_export_item_from_api,
	redact_export_item,
	render_html_export,
	write_export_file,
	write_html_export,
)
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchUrlParseError,
	ensure_supported_city,
	parse_boss_search_url,
	prefilter_platform_job_type,
	resolve_search_code_params,
)


_EXPORT_FILTER_PAGE_ALLOWANCE = 5


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
@click.option("--count", default=50, type=click.IntRange(min=1), help="导出数量")
@click.option("--format", "fmt", default="csv", type=click.Choice(["html", "csv", "json"]), help="输出格式")
@click.option("--output", "-o", default=None, help="输出文件路径（csv/json 不指定时输出到 stdout JSON 信封；html 不指定时直出 HTML，--json 时放进 data.content）")
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
	count: int,
	fmt: str,
	output: str | None,
	include_private: bool,
) -> None:
	"""导出搜索结果为 HTML / CSV / JSON 文件"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	raw_params: dict[str, str] = {}

	if search_url:
		try:
			parsed_url = parse_boss_search_url(search_url)
		except SearchUrlParseError as exc:
			handle_error_output(ctx, "export", code="INVALID_PARAM", message=str(exc))
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
			recovery_action="boss export <query> 或 boss export --url <搜索页URL>",
		)
		return
	query = query or ""

	try:
		ensure_supported_city(city)
	except ValueError as exc:
		handle_error_output(
			ctx,
			"export",
			code="INVALID_PARAM",
			message=str(exc),
			hints={"next_actions": [boss_command_for_ctx(ctx, "cities")]},
		)
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
		handle_error_output(ctx, "export", code="INVALID_PARAM", message=str(exc))
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

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		all_items: list[dict[str, Any]] = []
		html_items: list[dict[str, Any]] = []
		html_mode = fmt == "html"
		collected = html_items if html_mode else all_items
		page = 1
		# 本地严格筛选可能剔除整页结果，额外扫描固定页数；上限仍由用户
		# 显式请求的 count 决定，避免 hasMore 异常时形成无界请求。
		estimated_pages = max(1, (count + 14) // 15)
		max_pages = estimated_pages + _EXPORT_FILTER_PAGE_ALLOWANCE

		while len(collected) < count and page <= max_pages:
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
			platform_data = platform.unwrap_data(raw) or {}
			job_list = field_list(platform_data, "jobList")
			if not job_list:
				break

			for raw_item in job_list:
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

			if not platform_data.get("hasMore", False):
				break
			page += 1

		if output:
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
			}
			handle_output(
				ctx,
				"export",
				data,
				render=lambda d: render_export_summary(d),
				hints={
					"next_actions": [
						boss_command_for_ctx(ctx, "search <query>"),
						boss_command_for_ctx(ctx, "recommend"),
					],
				},
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
					},
					hints={"next_actions": [boss_command_for_ctx(ctx, "export <query> --format html -o <path>")],
						"operator_actions": ["把 data.content 写成 .html 文件后打开"]},
				)
			else:
				# stdout 直出 HTML（便于 `boss export <query> --format html > jobs.html`）
				sys.stdout.write(html_text)
				sys.stdout.flush()
		else:
			write_items = all_items if include_private else [redact_export_item(item) for item in all_items]
			data = {
				"count": len(all_items),
				"format": fmt,
				"jobs": write_items,
			}
			handle_output(
				ctx,
				"export",
				data,
				render=lambda d: render_job_table(d.get("jobs", []), "export"),
				hints={
					"next_actions": [
						boss_command_for_ctx(ctx, "export <query> -o file.csv"),
					],
				},
			)


def _private_fields_state(*, fmt: str, include_private: bool) -> str:
	if fmt == "html":
		return "omitted"
	return "included" if include_private else "redacted"
