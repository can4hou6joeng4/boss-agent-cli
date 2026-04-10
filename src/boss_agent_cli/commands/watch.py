import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.endpoints import (
	CITY_CODES,
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output
from boss_agent_cli.search_filters import SearchFilterCriteria, resolve_welfare_keywords, run_search_pipeline


def _parse_watch_filters(query, city, salary, experience, education, industry, scale, stage, job_type, welfare) -> tuple[dict, list[tuple[str, list[str]]] | None]:
	params = {
		"query": query,
		"city": city,
		"salary": salary,
		"experience": experience,
		"education": education,
		"industry": industry,
		"scale": scale,
		"stage": stage,
		"job_type": job_type,
		"welfare": welfare,
	}
	welfare_conditions = None
	if welfare:
		labels = [w.strip() for w in welfare.split(",") if w.strip()]
		welfare_conditions = [(label, resolve_welfare_keywords(label)) for label in labels]
	return params, welfare_conditions


@click.group("watch")
def watch_group():
	"""保存搜索条件并执行增量监控。"""


@watch_group.command("add")
@click.argument("name")
@click.argument("query")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--experience", default=None, help="经验要求")
@click.option("--education", default=None, help="学历要求")
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), help="行业类型")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型")
@click.option("--welfare", default=None, help="福利筛选")
@click.pass_context
def watch_add_cmd(ctx, name, query, city, salary, experience, education, industry, scale, stage, job_type, welfare):
	if city and city not in CITY_CODES:
		handle_error_output(ctx, "watch", code="INVALID_PARAM", message=f"未知城市: {city}")
		return

	params, _ = _parse_watch_filters(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)
	handle_output(
		ctx, "watch",
		{"action": "add", "name": name, "params": params},
		hints={"next_actions": [f"boss watch run {name}", "boss watch list"]},
	)


@watch_group.command("list")
@click.pass_context
def watch_list_cmd(ctx):
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items = cache.list_saved_searches()
	handle_output(ctx, "watch", items, hints={"next_actions": ["boss watch run <name>", "boss watch remove <name>"]})


@watch_group.command("remove")
@click.argument("name")
@click.pass_context
def watch_remove_cmd(ctx, name):
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.delete_saved_search(name)
	handle_output(ctx, "watch", {"action": "remove", "name": name, "removed": removed})


@watch_group.command("run")
@click.argument("name")
@click.pass_context
@handle_auth_errors("watch")
def watch_run_cmd(ctx, name):
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")

	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		record = cache.get_saved_search(name)
		if record is None:
			handle_error_output(ctx, "watch", code="JOB_NOT_FOUND", message=f"未找到 watch: {name}")
			return

		params = record["params"]
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
		auth = AuthManager(data_dir, logger=logger)
		with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
			pipeline_result = run_search_pipeline(
				client,
				cache,
				logger,
				criteria=criteria,
				start_page=1,
				max_pages=5 if welfare_conditions else 1,
				welfare_conditions=welfare_conditions,
			)
		watch_result = cache.record_watch_results(name, pipeline_result.items)
		handle_output(
			ctx,
			"watch",
			{
				"name": name,
				"new_count": watch_result["new_count"],
				"seen_count": watch_result["seen_count"],
				"total_count": watch_result["total_count"],
				"new_items": watch_result["new_items"],
			},
			hints={"next_actions": ["boss detail <security_id>", "boss watch list"]},
		)
