import click
from typing import Any

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, handle_output, render_simple_list, shell_argument


def _hint_command(ctx: click.Context, command: str) -> str:
	"""候选池后继操作沿用存放该条目的数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


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
	"""把一个职位加入本地候选池，可同时写标签与备注。"""
	# 空字符串仍是合法的 argv，但不构成一条候选记录：存进去之后 `shortlist list`
	# 会多出一行全空数据，提示里的 `boss shortlist remove ` 也没有可执行的参数。
	# Agent 从搜索结果里取字段失败时最容易走到这里，必须在写盘前拒绝。
	if not security_id.strip() or not job_id.strip():
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="security_id 与 job_id 不能为空",
			recovery_action=f"补上两个编号后重试；编号可从 {search_cmd} 的结果中取到",
			hints={"next_actions": [search_cmd]},
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
				_hint_command(ctx, "shortlist list"),
				_hint_command(ctx, f"shortlist remove {shell_argument(security_id)} {shell_argument(job_id)}"),
			]
		},
	)


@shortlist_group.command("list")
@click.pass_context
def shortlist_list_cmd(ctx: click.Context) -> None:
	"""列出本地候选池中的全部职位。"""
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
		hints={"next_actions": [_hint_command(ctx, "shortlist add <security_id> <job_id>")]
		if not items
		else [_hint_command(ctx, "detail <security_id> --job-id <job_id>")]},
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
	"""给候选池条目增删标签或替换备注；条目不存在时报错而不是静默成功。"""
	# 空标识不可能命中候选池里的任何条目：继续查下去会报 SHORTLIST_ENTRY_NOT_FOUND
	# 「候选池中没有该职位:  / 」，把 argv 少传参数说成「这条职位不在池子里」。
	if not security_id.strip() or not job_id.strip():
		list_cmd = _hint_command(ctx, "shortlist list")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="security_id 与 job_id 不能为空",
			recovery_action=f"补上两个编号后重试；候选池条目的编号可用 {list_cmd} 查看",
			hints={"next_actions": [list_cmd]},
		)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		current = _find_shortlist_item(cache.list_shortlist(), security_id, job_id)
		if current is None:
			# 不在候选池里的职位没有可标注的记录：以前返回 ok:true / updated:false，
			# Agent 会以为标签和备注已经写进去了。
			handle_error_output(
				ctx,
				"shortlist",
				code="SHORTLIST_ENTRY_NOT_FOUND",
				message=f"候选池中没有该职位: {security_id} / {job_id}",
				hints={
					"next_actions": [
						_hint_command(ctx, f"shortlist add {shell_argument(security_id)} {shell_argument(job_id)}"),
						_hint_command(ctx, "shortlist list"),
					]
				},
			)
			return
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
			"updated": True,
			"item": current,
		},
		hints={"next_actions": [_hint_command(ctx, "shortlist compare")]},
	)


@shortlist_group.command("compare")
@click.option("--tag", default=None, help="只比较包含该本地标签的候选职位")
@click.pass_context
def shortlist_compare_cmd(ctx: click.Context, tag: str | None) -> None:
	"""并排比较候选池条目（可按标签过滤）。"""
	normalized_tag = tag.strip() if tag else None
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		pool = cache.list_shortlist()
	items = pool
	if normalized_tag:
		items = [item for item in items if normalized_tag in item.get("tags", [])]
	compare_items = [_compare_item(item) for item in items]
	# 带 --tag 时为空有两种原因：候选池本来就是空的，或者只是没有条目带这个标签。
	# 后者再提示 `shortlist add` 是错的——池子里明明有条目，缺的是标签或一次不带
	# 过滤的比较。按池子是否为空分别给下一步。
	if not compare_items and normalized_tag and pool:
		next_actions = [
			_hint_command(ctx, "shortlist compare"),
			_hint_command(ctx, "shortlist list"),
		]
	elif not compare_items:
		next_actions = [_hint_command(ctx, "shortlist add <security_id> <job_id>")]
	else:
		next_actions = [_hint_command(ctx, "shortlist annotate <security_id> <job_id>")]
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
		hints={"next_actions": next_actions},
	)


@shortlist_group.command("remove")
@click.argument("security_id")
@click.argument("job_id")
@click.pass_context
def shortlist_remove_cmd(ctx: click.Context, security_id: str, job_id: str) -> None:
	"""从本地候选池中移除一个职位。"""
	# watch remove / preset remove 早已拒绝空名字：同样的空标识在候选池里既不
	# 会命中条目，又会返回 ok:true + removed:false，Agent 只看 ok 会以为删掉了。
	if not security_id.strip() or not job_id.strip():
		list_cmd = _hint_command(ctx, "shortlist list")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="security_id 与 job_id 不能为空",
			recovery_action=f"补上两个编号后重试；候选池条目的编号可用 {list_cmd} 查看",
			hints={"next_actions": [list_cmd]},
		)
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.remove_shortlist(security_id, job_id)
	handle_output(
		ctx,
		"shortlist",
		{"action": "remove", "security_id": security_id, "job_id": job_id, "removed": removed},
		hints={"next_actions": [_hint_command(ctx, "shortlist list")]},
	)
