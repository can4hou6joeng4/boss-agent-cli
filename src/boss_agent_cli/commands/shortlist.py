import click
from typing import Any

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_output, render_simple_list


def _parse_tags(value: str) -> list[str]:
	tags: list[str] = []
	seen: set[str] = set()
	for raw in value.split(","):
		tag = raw.strip()
		if not tag or tag in seen:
			continue
		tags.append(tag)
		seen.add(tag)
	return tags


def _merge_tags(current: list[str], add_tags: tuple[str, ...], remove_tags: tuple[str, ...]) -> list[str]:
	tags = _parse_tags(",".join([*current, *add_tags]))
	remove = set(_parse_tags(",".join(remove_tags)))
	return [tag for tag in tags if tag not in remove]


def _find_shortlist_item(items: list[dict[str, Any]], security_id: str, job_id: str) -> dict[str, Any] | None:
	for item in items:
		if item.get("security_id") == security_id and item.get("job_id") == job_id:
			return item
	return None


def _compare_item(item: dict[str, Any]) -> dict[str, Any]:
	return {
		"security_id": item.get("security_id", ""),
		"job_id": item.get("job_id", ""),
		"lid": item.get("lid", ""),
		"title": item.get("title", ""),
		"company": item.get("company", ""),
		"city": item.get("city", ""),
		"salary": item.get("salary", ""),
		"tags": item.get("tags", []),
		"note": item.get("note", ""),
	}


@click.group("shortlist")
def shortlist_group() -> None:
	"""管理职位候选池。"""


@shortlist_group.command("add")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--title", default="", help="职位名称")
@click.option("--company", default="", help="公司名称")
@click.option("--city", default="", help="城市")
@click.option("--salary", default="", help="薪资")
@click.option("--source", default="manual", help="来源，如 search/recommend/show/manual")
@click.option("--lid", default="", help="与 security_id 同一列表项的访问上下文（可选；不推断有效期）")
@click.option("--tags", default="", help="本地标签，逗号分隔")
@click.option("--note", default="", help="本地备注")
@click.pass_context
def shortlist_add_cmd(
	ctx: click.Context,
	security_id: str,
	job_id: str,
	title: str,
	company: str,
	city: str,
	salary: str,
	source: str,
	lid: str,
	tags: str,
	note: str,
) -> None:
	# 空字符串仍是合法的 argv，但不构成一条候选记录：存进去之后 `shortlist list`
	# 会多出一行全空数据，提示里的 `boss shortlist remove ` 也没有可执行的参数。
	# Agent 从搜索结果里取字段失败时最容易走到这里，必须在写盘前拒绝。
	if not security_id.strip() or not job_id.strip():
		handle_error_output(
			ctx, "shortlist", code="INVALID_PARAM", message="security_id 与 job_id 不能为空"
		)
		return
	parsed_tags = _parse_tags(tags)
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		stored = cache.add_shortlist(
			{
				"security_id": security_id,
				"job_id": job_id,
				"title": title,
				"company": company,
				"city": city,
				"salary": salary,
				"source": source,
				"lid": lid,
				"tags": parsed_tags,
				"note": note,
			}
		)
	# 回显存储后的行，而不是入参：重复加入同一职位时本地标签/备注会保留下来，
	# 入参里没给这些字段（空字符串），直接回显会让 Agent 以为资料被清空了。
	handle_output(
		ctx,
		"shortlist",
		{"action": "add", **stored},
		hints={
			"next_actions": [
				boss_command_for_ctx(ctx, "shortlist list"),
				boss_command_for_ctx(ctx, f"shortlist remove {security_id} {job_id}"),
			]
		},
	)


@shortlist_group.command("list")
@click.pass_context
def shortlist_list_cmd(ctx: click.Context) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items = cache.list_shortlist()
	handle_output(
		ctx,
		"shortlist",
		items,
		render=lambda data: render_simple_list(
			data,
			"shortlist",
			[
				("title", "title", "bold cyan"),
				("company", "company", "green"),
				("city", "city", "yellow"),
				("salary", "salary", "dim"),
				("tags", "tags", "blue"),
				("note", "note", "white"),
				("source", "source", "magenta"),
			],
		),
		hints={"next_actions": [boss_command_for_ctx(ctx, "detail <security_id> --job-id <job_id>")]},
	)


@shortlist_group.command("annotate")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--add-tag", multiple=True, help="添加本地标签，可重复")
@click.option("--remove-tag", multiple=True, help="移除本地标签，可重复")
@click.option("--note", default=None, help="替换本地备注")
@click.pass_context
def shortlist_annotate_cmd(
	ctx: click.Context,
	security_id: str,
	job_id: str,
	add_tag: tuple[str, ...],
	remove_tag: tuple[str, ...],
	note: str | None,
) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		current = _find_shortlist_item(cache.list_shortlist(), security_id, job_id)
		updated = current is not None
		if current:
			tags = _merge_tags(current.get("tags", []), add_tag, remove_tag)
			cache.set_shortlist_tags(security_id, job_id, tags)
			if note is not None:
				cache.set_shortlist_note(security_id, job_id, note)
			current = _find_shortlist_item(cache.list_shortlist(), security_id, job_id)
	handle_output(
		ctx,
		"shortlist",
		{
			"action": "annotate",
			"security_id": security_id,
			"job_id": job_id,
			"updated": updated,
			"item": current,
		},
		hints={"next_actions": [boss_command_for_ctx(ctx, "shortlist compare")]},
	)


@shortlist_group.command("compare")
@click.option("--tag", default=None, help="只比较包含该本地标签的候选职位")
@click.pass_context
def shortlist_compare_cmd(ctx: click.Context, tag: str | None) -> None:
	normalized_tag = tag.strip() if tag else None
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items = cache.list_shortlist()
	if normalized_tag:
		items = [item for item in items if normalized_tag in item.get("tags", [])]
	compare_items = [_compare_item(item) for item in items]
	handle_output(
		ctx,
		"shortlist",
		{
			"tag": normalized_tag,
			"count": len(compare_items),
			"items": compare_items,
		},
		render=lambda data: render_simple_list(
			data["items"],
			"shortlist compare",
			[
				("title", "title", "bold cyan"),
				("company", "company", "green"),
				("salary", "salary", "yellow"),
				("tags", "tags", "blue"),
				("note", "note", "white"),
			],
		),
		hints={"next_actions": [boss_command_for_ctx(ctx, "shortlist annotate <security_id> <job_id>")]},
	)


@shortlist_group.command("remove")
@click.argument("security_id")
@click.argument("job_id")
@click.pass_context
def shortlist_remove_cmd(ctx: click.Context, security_id: str, job_id: str) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.remove_shortlist(security_id, job_id)
	handle_output(
		ctx,
		"shortlist",
		{"action": "remove", "security_id": security_id, "job_id": job_id, "removed": removed},
		hints={"next_actions": [boss_command_for_ctx(ctx, "shortlist list")]},
	)
