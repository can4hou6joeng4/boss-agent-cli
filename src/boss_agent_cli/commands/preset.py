import click
from typing import Any

from boss_agent_cli.api.endpoints import (
	INDUSTRY_CODES,
	JOB_TYPE_CODES,
	SCALE_CODES,
	STAGE_CODES,
)
from boss_agent_cli.cache.store import CacheStore, saved_search_name_error
from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_output, render_action_result, render_saved_search_list, shell_argument
from boss_agent_cli.search_filters import (
	ACTIVE_LEVELS,
	build_search_params,
	ensure_supported_city,
	ensure_text_encodable,
	resolve_active_level,
	resolve_search_code_params,
)
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.services.saved_searches import configured_search_params, validate_stored_search_params


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


def _hint_command(ctx: click.Context, command: str) -> str:
	"""preset 的后继命令必须保留原数据目录与平台。

	预设只存在当前数据目录的本地缓存里。提示里写裸的 ``boss search --preset <名称>``
	时，使用自定义 ``--data-dir`` 的调用方照抄会落到默认目录：刚保存的预设立刻
	``SAVED_SEARCH_NOT_FOUND``，随后的「重建」提示还会在默认目录里另建一份
	（Issue #919）。
	"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


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
	"""保存一组可复用的搜索参数，之后用 `boss search --preset <名称>` 调用。"""
	# 空字符串是合法 argv，但存下来的预设既没法点名（`boss search --preset ""`），
	# 也没法搜到目标（空关键词等于不筛选）。必须在写盘前拒绝。
	if not name.strip() or not query.strip():
		list_cmd = _hint_command(ctx, "preset list")
		handle_error_output(
			ctx,
			"preset",
			code="INVALID_PARAM",
			message="preset 名称与搜索关键词不能为空",
			recovery_action=f"补上预设名称和搜索关键词后重试；用 {list_cmd} 查看已有预设",
			hints={"next_actions": [list_cmd]},
		)
		return
	if problem := saved_search_name_error(name):
		list_cmd = _hint_command(ctx, "preset list")
		handle_error_output(
			ctx,
			"preset",
			code="INVALID_PARAM",
			message=f"预设{problem}",
			recovery_action=f"换一个只含普通 Unicode 文本的名称重试；用 {list_cmd} 查看已有预设",
			hints={"next_actions": [list_cmd]},
		)
		return
	# 与 `watch add` 一致：保存时就把筛选条件校验掉。未知薪资 / 经验 / 学历
	# 写进预设后，`search --preset` 每次都要先在参数解析处失败，而预设本身
	# 仍显示为「已保存」，用户很难看出该改的是这条本地记录。
	try:
		# 与 watch add 同口径：关键词最终进入 httpx 的 URL 参数，孤立代理项
		# 会在组参数时抛 UnicodeEncodeError（Issue #2528）。
		ensure_text_encodable("搜索关键词", query)
		ensure_supported_city(city)
		resolve_search_code_params(
			salary=salary, experience=experience, education=education,
			industry=industry, scale=scale, stage=stage, job_type=job_type,
		)
	except ValueError as exc:
		emit_filter_error(ctx, "preset", exc)
		return
	try:
		active_level = resolve_active_level(active)
	except ValueError as exc:
		emit_filter_error(ctx, "preset", exc)
		return

	params = _build_params(query, city, salary, experience, education, industry, scale, stage, job_type, welfare)
	if active_level:
		# 只在设置时写入，旧预设和不带 --active 的预设保持原有 10 个键
		params["active"] = active_level
	# 与读取侧（search --preset / watch run）共用同一份存量校验：入口此前只
	# 校验关键词的可编码性，`--welfare "\udcff"` 之类能保存成功，随后每次
	# 运行都以 SAVED_SEARCH_INVALID 拒绝，删除后按同样参数重建仍然失败
	# （Issue #2543）。
	try:
		validate_stored_search_params(params)
	except ValueError as exc:
		emit_filter_error(ctx, "preset", exc)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)
	next_actions = [
		_hint_command(ctx, f"search --preset {shell_argument(name)}"),
		_hint_command(ctx, "preset list"),
	]
	handle_output(
		ctx,
		"preset",
		{"action": "add", "name": name, "params": params},
		render=lambda d: render_action_result(
			{**d, "params": configured_search_params(d["params"])}, title="preset", next_steps=next_actions
		),
		hints={"next_actions": next_actions},
	)


@preset_group.command("list")
@click.pass_context
def preset_list_cmd(ctx: click.Context) -> None:
	"""列出本地已保存的搜索预设。"""
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items, corrupt = cache.list_saved_searches_with_corrupt()
	add_cmd = _hint_command(ctx, "preset add <name> <关键词>")
	search_cmd = _hint_command(ctx, "search --preset <name>")
	remove_cmd = _hint_command(ctx, "preset remove <name>")
	hints: dict[str, Any] = {
		# 空列表里没有 name 可搜索 / 删除：照抄只会换回一个不存在的预设。
		"next_actions": [add_cmd] if not items else [search_cmd, remove_cmd]
	}
	if corrupt:
		# 坏条目不能假装不存在：列表照常返回可用项，同时指明该删哪个。
		hints["corrupt"] = corrupt
		hints["operator_actions"] = [
			f"以下预设的参数已损坏，先执行 {_hint_command(ctx, 'preset remove <名称>')} 再重新 preset add：{', '.join(corrupt)}"
		]
	handle_output(
		ctx,
		"preset",
		items,
		render=lambda d: render_saved_search_list(
			d,
			"presets",
			next_steps=[add_cmd] if not d else [search_cmd, remove_cmd],
		),
		hints=hints,
	)


@preset_group.command("remove")
@click.argument("name")
@click.pass_context
def preset_remove_cmd(ctx: click.Context, name: str) -> None:
	"""删除一条本地搜索预设；名字不存在时保持幂等（不报错）。"""
	# 与 add 同口径：空名字不可能命中任何本地记录，却会以
	# `{"removed": false}` 的 `ok: true` 报成功，Agent 会以为已经删掉了。
	if not name.strip():
		list_cmd = _hint_command(ctx, "preset list")
		handle_error_output(
			ctx,
			"preset",
			code="INVALID_PARAM",
			message="预设名称不能为空",
			recovery_action=f"补上预设名称后重试；用 {list_cmd} 查看已有预设",
			hints={"next_actions": [list_cmd]},
		)
		return
	if problem := saved_search_name_error(name):
		list_cmd = _hint_command(ctx, "preset list")
		handle_error_output(
			ctx,
			"preset",
			code="INVALID_PARAM",
			message=f"预设{problem}",
			recovery_action=f"换一个只含普通 Unicode 文本的名称重试；用 {list_cmd} 查看已有预设",
			hints={"next_actions": [list_cmd]},
		)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.delete_saved_search(name)
	handle_output(
		ctx,
		"preset",
		{"action": "remove", "name": name, "removed": removed},
		render=lambda d: render_action_result(d, title="preset", next_steps=[_hint_command(ctx, "preset list")]),
		hints={"next_actions": [_hint_command(ctx, "preset list")]},
	)
