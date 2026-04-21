import json

import click

from boss_agent_cli.api.endpoints import (
	CITY_CODES,
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_job_table
from boss_agent_cli.index_cache import try_save_index
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	resolve_welfare_keywords,
	run_search_pipeline,
)


@click.command("search")
@click.argument("query", required=False)
@click.option("--preset", default=None, help="预设名称（从 boss preset add 保存）")
@click.option("--city", default=None, help="城市名称（如 北京、上海）")
@click.option("--salary", default=None, help="薪资范围（如 10-20K）")
@click.option("--experience", default=None, help="经验要求（如 3-5年）")
@click.option("--education", default=None, help="学历要求（如 本科）")
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), help="行业类型")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模（如 100-499人）")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段（如 已上市、A轮）")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型（全职/兼职/实习）")
@click.option("--welfare", default=None, help="福利筛选（如 双休、五险一金），会逐个检查职位详情")
@click.option("--page", default=1, help="页码")
@click.option("--no-cache", is_flag=True, default=False, help="跳过缓存")
@click.option("--with-score", is_flag=True, default=False, help="附加匹配分和原因")
@click.pass_context
@handle_auth_errors("search")
def search_cmd(ctx: click.Context, query: str, preset: str | None, city: str | None, salary: str | None, experience: str | None, education: str | None, industry: str | None, scale: str | None, stage: str | None, job_type: str | None, welfare: str | None, page: int, no_cache: bool, with_score: bool) -> None:
	"""按关键词和筛选条件搜索职位列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	if preset:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			record = cache.get_saved_search(preset)
		if record is None:
			handle_error_output(ctx, "search", code="JOB_NOT_FOUND", message=f"未找到 preset: {preset}")
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

	if not query:
		handle_error_output(ctx, "search", code="INVALID_PARAM", message="未提供 query，请传入搜索关键词或 --preset")
		return

	if city and city not in CITY_CODES:
		handle_error_output(
			ctx, "search",
			code="INVALID_PARAM",
			message=f"未知城市: {city}，请使用 CITY_CODES 中的城市名",
		)
		return

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
	)

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		# 有福利筛选时跳过缓存（因为需要逐个查详情）
		if not welfare_conditions and not no_cache and not with_score:
			search_params = {
				"query": query, "city": city, "salary": salary,
				"experience": experience, "education": education,
				"industry": industry, "scale": scale, "stage": stage,
				"job_type": job_type, "page": page,
			}
			cached = cache.get_search(search_params)
			if cached is not None:
				logger.debug("搜索命中缓存")
				result = json.loads(cached)
				handle_output(
					ctx, "search", result["data"],
					render=lambda data: render_job_table(data, f"search: {query}"),
					pagination=result.get("pagination"), hints=result.get("hints"),
				)
				return

		auth = AuthManager(data_dir, logger=logger)
		with get_platform_instance(ctx, auth) as platform:
			max_pages = 5 if welfare_conditions else 1
			pipeline_result = run_search_pipeline(
				platform, cache, logger,
				criteria=criteria,
				start_page=page,
				max_pages=max_pages,
				welfare_conditions=welfare_conditions,
			)
			items = pipeline_result.items
			if with_score:
				items = [score_job_dict(item, criteria=criteria, expect_data=None) for item in items]
			try_save_index(data_dir, items, source=f"search:{query}", logger=logger)

			# Emit search_completed hook
			hooks = ctx.obj.get("hooks")
			if hooks:
				hooks.search_completed.call({
					"query": query,
					"page": page,
					"result_count": len(items),
					"stats": {
						"pages_scanned": pipeline_result.stats.pages_scanned,
						"jobs_seen": pipeline_result.stats.jobs_seen,
						"jobs_prefiltered": pipeline_result.stats.jobs_prefiltered,
						"detail_checks": pipeline_result.stats.detail_checks,
					},
					"source": "search",
				})

			pagination = {
				"page": page,
				"has_more": pipeline_result.has_more,
				"total": pipeline_result.total or len(items),
			}
			hints = {
				"next_actions": [
					"使用 boss detail <security_id> 查看职位详情",
					"使用 boss greet <security_id> <job_id> 打招呼",
				],
			}
			if pipeline_result.has_more and not welfare_conditions:
				hints["next_actions"].append(
					f"使用 boss search <query> --page {page + 1} 查看下一页"
				)

			# 缓存普通搜索结果
			if not welfare_conditions and not with_score:
				search_params = {
					"query": query, "city": city, "salary": salary,
					"experience": experience, "education": education,
					"industry": industry, "scale": scale, "stage": stage,
					"job_type": job_type, "page": page,
				}
				cache_data = {"data": items, "pagination": pagination, "hints": hints}
				cache.put_search(search_params, json.dumps(cache_data, ensure_ascii=False))

			title_suffix = " (welfare filter)" if welfare_conditions else ""
			handle_output(
				ctx, "search", items,
				render=lambda data: render_job_table(
					data, f"search: {query}{title_suffix}",
					page=page,
					hint_next=f"more: boss search \"{query}\" --page {page + 1}" if pipeline_result.has_more and not welfare_conditions else "",
				),
				pagination=pagination, hints=hints,
			)
