from typing import Any

import click

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_error_output,
	handle_output,
	render_export_summary,
	render_job_table,
)
from boss_agent_cli.services.job_export import (
	prepare_export_items,
	public_html_export_item_from_api,
	redact_export_item,
	write_export_file,
	write_html_export,
)
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchUrlParseError,
	parse_boss_search_url,
	prefilter_platform_job_type,
	resolve_search_code_params,
)


_HTML_PUBLIC_EXPORT_FIELDS = ("title", "company", "city", "experience", "education", "skills", "welfare")
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
@click.option("--count", default=50, type=int, help="导出数量")
@click.option("--format", "fmt", default="csv", type=click.Choice(["html", "csv", "json"]), help="输出格式")
@click.option("--output", "-o", default=None, help="输出文件路径（不指定则输出到 stdout JSON 信封）")
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
	"""导出搜索结果为 CSV 或 JSON 文件"""
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
			recoverable=True,
			recovery_action="boss export <query> 或 boss export --url <搜索页URL>",
		)
		return
	query = query or ""

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
		html_file_output = bool(output and fmt == "html")
		page = 1
		# 本地严格筛选可能剔除整页结果，额外扫描固定页数；上限仍由用户
		# 显式请求的 count 决定，避免 hasMore 异常时形成无界请求。
		estimated_pages = max(1, (count + 14) // 15)
		max_pages = estimated_pages + _EXPORT_FILTER_PAGE_ALLOWANCE

		while (
			_export_item_count(all_items, html_items, html_file_output=html_file_output) < count and page <= max_pages
		):
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
				code, message = platform.parse_error(raw)
				handle_error_output(
					ctx,
					"export",
					code=code,
					message=message or "搜索结果获取失败",
					recoverable=False,
				)
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = platform_data.get("jobList", [])
			if not job_list:
				break

			for raw_item in job_list:
				if _export_item_count(all_items, html_items, html_file_output=html_file_output) >= count:
					break
				matches, _ = prefilter_platform_job_type(raw_item, criteria, platform_name=platform.name)
				if not matches:
					continue
				if html_file_output:
					html_items.append(public_html_export_item_from_api(raw_item))
				else:
					item = JobItem.from_api(raw_item)
					all_items.append(item.to_dict())

			if not platform_data.get("hasMore", False):
				break
			page += 1

		if output:
			if html_file_output:
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
						"boss search <query> — 继续搜索",
						"boss recommend — 获取个性化推荐",
					],
				},
			)
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
						"boss export <query> -o file.csv — 导出到文件",
					],
				},
			)


def _export_item_count(
	all_items: list[dict[str, Any]], html_items: list[dict[str, Any]], *, html_file_output: bool
) -> int:
	if html_file_output:
		return len(html_items)
	return len(all_items)


def _private_fields_state(*, fmt: str, include_private: bool) -> str:
	if fmt == "html":
		return "omitted"
	return "included" if include_private else "redacted"
