import click

from boss_agent_cli.api.endpoints import (
	CITY_CODES,
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_error_output, handle_output


def _build_params(
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
) -> dict[str, str | None]:
	return {
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


@click.group("preset")
def preset_group() -> None:
	"""管理可复用搜索预设。"""


@preset_group.command("add")
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
def preset_add_cmd(
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
	if city and city not in CITY_CODES:
		handle_error_output(ctx, "preset", code="INVALID_PARAM", message=f"未知城市: {city}")
		return

	params = _build_params(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)
	handle_output(
		ctx,
		"preset",
		{"action": "add", "name": name, "params": params},
		hints={"next_actions": [f"boss search --preset {name}", "boss preset list"]},
	)


@preset_group.command("list")
@click.pass_context
def preset_list_cmd(ctx: click.Context) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items = cache.list_saved_searches()
	handle_output(
		ctx,
		"preset",
		items,
		hints={"next_actions": ["boss search --preset <name>", "boss preset remove <name>"]},
	)


@preset_group.command("remove")
@click.argument("name")
@click.pass_context
def preset_remove_cmd(ctx: click.Context, name: str) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.delete_saved_search(name)
	handle_output(ctx, "preset", {"action": "remove", "name": name, "removed": removed})
