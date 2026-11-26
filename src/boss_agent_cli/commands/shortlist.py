import click
from typing import Any

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_error_output,
	handle_output,
	positional_args,
	render_action_result,
	render_simple_list,
)
from boss_agent_cli.services.text_files import is_utf8_encodable


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


def _reject_unencodable_fields(ctx: click.Context, fields: tuple[tuple[str, str], ...]) -> bool:
	"""在存储或匹配前拒绝无法按 UTF-8 编码的候选池参数。"""
	bad = dict.fromkeys(name for name, value in fields if not is_utf8_encodable(value))
	if not bad:
		return True
	list_cmd = _hint_command(ctx, "shortlist list")
	handle_error_output(
		ctx,
		"shortlist",
		code="INVALID_PARAM",
		message=f"{' / '.join(bad)} 含有无法按 UTF-8 编码的字符（孤立代理项），请使用有效的文本参数",
		recovery_action=f"重新复制这些字段后重试；候选池现有条目可用 {list_cmd} 查看",
		hints={"next_actions": [list_cmd]},
	)
	return False


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
@click.option(
	"--source", default=None,
	help="来源，如 search/recommend/show/manual；省略时新记录记为 manual，重复加入保留原来源",
)
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
	source: str | None,
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
	# 空白 --source 是「没设值」的脚本变量，不是一条来源：落库后 list/compare
	# 显示为空白来源，且 #2960 的保留语义会让它永久粘在记录上（Issues #2960 #2970）。
	# 与 `shortlist compare --tag ""` 等空白参数同口径，在写盘前按 INVALID_PARAM
	# 拒绝；省略 --source 才是「未指定」。
	if source is not None and not source.strip():
		list_cmd = _hint_command(ctx, "shortlist list")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="--source 不能为空或只含空白字符",
			recovery_action=(
				"省略 --source 表示未指定来源（新记录记为 manual，重复加入保留原来源），"
				f"或传入非空来源后重试；用 {list_cmd} 查看已有条目"
			),
			hints={"next_actions": [list_cmd]},
		)
		return
	if not _reject_unencodable_fields(
		ctx,
		(
			("security_id", security_id),
			("job_id", job_id),
			("--title", title),
			("--company", company),
			("--city", city),
			("--salary", salary),
			("--source", source or ""),
			("--lid", lid),
			("--tags", tags),
			("--note", note),
		),
	):
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
	next_actions = [
		_hint_command(ctx, "shortlist list"),
		_hint_command(ctx, f"shortlist remove {positional_args(security_id, job_id)}"),
	]
	handle_output(
		ctx,
		"shortlist",
		{"action": "add", **stored},
		render=lambda d: render_action_result(d, title="shortlist add", next_steps=next_actions),
		hints={"next_actions": next_actions},
	)


@shortlist_group.command("list")
@click.pass_context
def shortlist_list_cmd(ctx: click.Context) -> None:
	"""列出本地候选池中的全部职位。"""
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items, corrupt = cache.list_shortlist_with_corrupt()
	hints: dict[str, Any] = {
		"next_actions": [_hint_command(ctx, "shortlist add <security_id> <job_id>")]
		if not items
		else [_hint_command(ctx, "detail <security_id> --job-id <job_id>")]
	}
	if corrupt:
		# 坏条目不能假装不存在：可用项照常返回，同时指明该删哪些（Issue #2224，
		# 与 watch / preset 的 corrupt 名单同口径）。
		hints["corrupt"] = corrupt
		hints["operator_actions"] = [
			"以下候选记录的字段已损坏、无法读取，先执行 "
			f"{_hint_command(ctx, 'shortlist remove <security_id> <job_id>')} 再重新 shortlist add：{', '.join(corrupt)}"
		]
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
		hints=hints,
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
	"""给候选池条目增删标签或替换备注；至少提供一个修改参数，条目不存在时报错而不是静默成功。"""
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
	note_fields: tuple[tuple[str, str], ...] = (("--note", note),) if note is not None else ()
	if not _reject_unencodable_fields(
		ctx,
		(
			("security_id", security_id),
			("job_id", job_id),
			*note_fields,
			*(("--add-tag", value) for value in add_tag),
			*(("--remove-tag", value) for value in remove_tag),
		),
	):
		return
	# annotate 是一次显式修改：既没有标签也没给 note 时，旧实现仍返回
	# updated=true，Agent 会以为字段已经写入；空标签同样被 _parse_tags 丢掉，
	# 信封却报告成功（Issue #1565）。两者都要在查找条目与写库之前拒绝。
	blank_tag = next((value for value in (*add_tag, *remove_tag) if not _parse_tags(value)), None)
	if blank_tag is not None:
		list_cmd = _hint_command(ctx, "shortlist list")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="--add-tag / --remove-tag 的标签值不能为空或只含逗号 / 空白",
			recovery_action=f"传入非空标签后重试；候选池条目的现有标签可用 {list_cmd} 查看",
			hints={"next_actions": [list_cmd]},
		)
		return
	if not add_tag and not remove_tag and note is None:
		list_cmd = _hint_command(ctx, "shortlist list")
		compare_cmd = _hint_command(ctx, "shortlist compare")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="没有要修改的字段：至少提供 --add-tag、--remove-tag 或 --note",
			recovery_action=f"补上要修改的标签或备注后重试；可用 {list_cmd} 查看候选池条目",
			hints={"next_actions": [list_cmd, compare_cmd]},
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
						_hint_command(ctx, f"shortlist add {positional_args(security_id, job_id)}"),
						_hint_command(ctx, "shortlist list"),
					]
				},
			)
			return
		tags = _merge_tags(current.get("tags", []), add_tag, remove_tag)
		tags_updated = cache.set_shortlist_tags(security_id, job_id, tags)
		note_updated = True
		if note is not None:
			note_updated = cache.set_shortlist_note(security_id, job_id, note)
		if not (tags_updated and note_updated):
			# UPDATE 的 rowcount 是「有没有写进去」的唯一信号：security_id 以
			# BLOB 落库（#1885 的读取兼容）或行在 SELECT 与 UPDATE 之间被并发
			# 删除时，TEXT 参数匹配不到存储值，旧实现照样回 updated:true，正是
			# #1565 要消灭的「没写进去却报成功」（Issue #2135）。
			list_cmd = _hint_command(ctx, "shortlist list")
			handle_error_output(
				ctx,
				"shortlist",
				code="SHORTLIST_ENTRY_NOT_FOUND",
				message=f"候选池记录未能写入（可能已被删除或标识存储不一致）: {security_id} / {job_id}",
				recovery_action=f"先用 {list_cmd} 核对条目是否仍在候选池，再决定是否重新 add",
				hints={"next_actions": [list_cmd]},
			)
			return
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
		render=lambda d: render_action_result(
			d, title="shortlist annotate", next_steps=[_hint_command(ctx, "shortlist compare")]
		),
		hints={"next_actions": [_hint_command(ctx, "shortlist compare")]},
	)


@shortlist_group.command("compare")
@click.option("--tag", default=None, help="只比较包含该本地标签的候选职位")
@click.pass_context
def shortlist_compare_cmd(ctx: click.Context, tag: str | None) -> None:
	"""并排比较候选池条目（可按标签过滤）。"""
	# 显式空标签不是「不过滤」：旧实现把它 strip 成 None 后比较全部条目，
	# 调用方以为得到按标签筛选的结果（Issue #1565）。省略 --tag 才是全部。
	if tag is not None and not tag.strip():
		compare_cmd = _hint_command(ctx, "shortlist compare")
		list_cmd = _hint_command(ctx, "shortlist list")
		handle_error_output(
			ctx,
			"shortlist",
			code="INVALID_PARAM",
			message="--tag 不能为空或只含空白；省略该选项才会比较全部候选职位",
			recovery_action=f"去掉 --tag 比较全部，或传入非空标签；可用 {list_cmd} 查看条目标签",
			hints={"next_actions": [compare_cmd, list_cmd]},
		)
		return
	if tag is not None and not _reject_unencodable_fields(ctx, (("--tag", tag),)):
		return
	normalized_tag = tag.strip() if tag is not None else None
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
	if not _reject_unencodable_fields(
		ctx,
		(("security_id", security_id), ("job_id", job_id)),
	):
		return
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.remove_shortlist(security_id, job_id)
	next_actions = [_hint_command(ctx, "shortlist list")]
	handle_output(
		ctx,
		"shortlist",
		{"action": "remove", "security_id": security_id, "job_id": job_id, "removed": removed},
		render=lambda d: render_action_result(d, title="shortlist remove", next_steps=next_actions),
		hints={"next_actions": next_actions},
	)
