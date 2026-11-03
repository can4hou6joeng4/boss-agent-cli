import click
from typing import Any

from boss_agent_cli.api.endpoints import (
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_error_output, handle_output, render_action_result, render_list_result
from boss_agent_cli.search_filters import (
	ACTIVE_LEVELS,
	build_search_params,
	ensure_supported_city,
	resolve_active_level,
	resolve_search_code_params,
)


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
	return build_search_params(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)


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
@click.option("--industry", default=None, type=click.Choice(list(INDUSTRY_CODES.keys()), case_sensitive=False), metavar="INDUSTRY", help="行业类型（可选值见 boss schema）")
@click.option("--scale", default=None, type=click.Choice(list(SCALE_CODES.keys()), case_sensitive=False), help="公司规模")
@click.option("--stage", default=None, type=click.Choice(list(STAGE_CODES.keys()), case_sensitive=False), help="融资阶段")
@click.option("--job-type", default=None, type=click.Choice(list(JOB_TYPE_CODES.keys()), case_sensitive=False), help="职位类型")
@click.option("--welfare", default=None, help="福利筛选")
@click.option("--active", default=None, help="HR 活跃度下限：" + " / ".join(ACTIVE_LEVELS))
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
	active: str | None,
) -> None:
	# 空字符串是合法 argv，但存下来的预设既没法点名（`boss search --preset ""`），
	# 也没法搜到目标（空关键词等于不筛选）。必须在写盘前拒绝。
	if not name.strip() or not query.strip():
		handle_error_output(ctx, "preset", code="INVALID_PARAM", message="preset 名称与搜索关键词不能为空")
		return
	# 与 `watch add` 一致：保存时就把筛选条件校验掉。未知薪资 / 经验 / 学历
	# 写进预设后，`search --preset` 每次都要先在参数解析处失败，而预设本身
	# 仍显示为「已保存」，用户很难看出该改的是这条本地记录。
	try:
		ensure_supported_city(city)
		resolve_search_code_params(
			salary=salary, experience=experience, education=education,
			industry=industry, scale=scale, stage=stage, job_type=job_type,
		)
	except ValueError as exc:
		handle_error_output(ctx, "preset", code="INVALID_PARAM", message=str(exc))
		return
	try:
		active_level = resolve_active_level(active)
	except ValueError as exc:
		handle_error_output(ctx, "preset", code="INVALID_PARAM", message=str(exc))
		return

	params = _build_params(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	if active_level:
		# 只在设置时写入，旧预设和不带 --active 的预设保持原有 10 个键
		params["active"] = active_level
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)
	handle_output(
		ctx,
		"preset",
		{"action": "add", "name": name, "params": params},
		render=lambda d: render_action_result(
			d, title="preset", next_steps=[f"boss search --preset {name}", "boss preset list"]
		),
		hints={"next_actions": [f"boss search --preset {name}", "boss preset list"]},
	)


@preset_group.command("list")
@click.pass_context
def preset_list_cmd(ctx: click.Context) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items, corrupt = cache.list_saved_searches_with_corrupt()
	hints: dict[str, Any] = {"next_actions": ["boss search --preset <name>", "boss preset remove <name>"]}
	if corrupt:
		# 坏条目不能假装不存在：列表照常返回可用项，同时指明该删哪个。
		hints["corrupt"] = corrupt
		hints["operator_actions"] = [
			f"以下预设的参数已损坏，先执行 boss preset remove <名称> 再重新 preset add：{', '.join(corrupt)}"
		]
	handle_output(
		ctx,
		"preset",
		items,
		render=lambda d: render_list_result(
			d,
			"presets",
			[("名称", "name", "bold cyan"), ("条件", "params", "green"), ("创建时间", "created_at", "dim")],
			next_steps=["boss preset add <name> <关键词>"] if not d else [
				"boss search --preset <name>",
				"boss preset remove <name>",
			],
		),
		hints=hints,
	)


@preset_group.command("remove")
@click.argument("name")
@click.pass_context
def preset_remove_cmd(ctx: click.Context, name: str) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.delete_saved_search(name)
	handle_output(
		ctx,
		"preset",
		{"action": "remove", "name": name, "removed": removed},
		render=lambda d: render_action_result(d, title="preset", next_steps=["boss preset list"]),
	)
