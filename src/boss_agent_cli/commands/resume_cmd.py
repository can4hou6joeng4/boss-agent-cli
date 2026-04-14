import json
from pathlib import Path

import click

from boss_agent_cli.display import handle_error_output, handle_output
from boss_agent_cli.resume.models import ResumeData, PersonalInfoSection, resume_to_dict
from boss_agent_cli.resume.store import ResumeStore


def _get_store(ctx) -> ResumeStore:
	resumes_dir: Path = ctx.obj["data_dir"] / "resumes"
	return ResumeStore(resumes_dir)


def _build_default_template(name: str) -> ResumeData:
	"""从默认模板创建空白简历"""
	return ResumeData(
		name=name,
		title="我的简历",
		center_title=False,
		personal_info=PersonalInfoSection(items=[], layout="inline"),
		job_intention=None,
		modules=[],
		avatar="",
	)


def _set_nested(data: dict, path: str, value: str) -> bool:
	"""通过 . 分隔路径设置嵌套 dict 字段，返回是否成功"""
	keys = path.split(".")
	current = data
	for key in keys[:-1]:
		if isinstance(current, dict) and key in current:
			current = current[key]
		else:
			return False
	last = keys[-1]
	if isinstance(current, dict) and last in current:
		current[last] = value
		return True
	return False


def _flat_diff(left: dict, right: dict, prefix: str = "") -> list[dict]:
	"""对比两个 dict 的差异，返回 [{field, left, right}, ...]"""
	diffs: list[dict] = []
	all_keys = sorted(set(list(left.keys()) + list(right.keys())))
	for key in all_keys:
		full_key = f"{prefix}.{key}" if prefix else key
		lv = left.get(key)
		rv = right.get(key)
		if isinstance(lv, dict) and isinstance(rv, dict):
			diffs.extend(_flat_diff(lv, rv, full_key))
		elif isinstance(lv, list) and isinstance(rv, list):
			if lv != rv:
				diffs.append({"field": full_key, "left": lv, "right": rv})
		elif lv != rv:
			diffs.append({"field": full_key, "left": lv, "right": rv})
	return diffs


@click.group("resume")
def resume_group():
	"""本地简历管理。"""


@resume_group.command("init")
@click.option("--name", default=None, help="简历名称")
@click.option("--template", default=None, help="模板名称（如 default）")
@click.pass_context
def resume_init_cmd(ctx, name, template):
	"""从默认模板初始化本地简历"""
	store = _get_store(ctx)
	if template is None:
		# TODO: 从 BOSS 直聘 API 拉取简历（需登录态）
		template = "default"
	if name is None:
		name = "default"
	if store.exists(name):
		handle_error_output(
			ctx, "resume",
			code="RESUME_ALREADY_EXISTS",
			message=f"简历 '{name}' 已存在",
			recovery_action="使用不同名称或先删除已有简历",
		)
		ctx.exit(1)
		return
	resume = _build_default_template(name)
	store.save(resume)
	handle_output(
		ctx, "resume",
		{"action": "init", "name": name, "template": template},
		hints={"next_actions": [f"boss resume show {name}", f"boss resume edit {name} --field title --value <新标题>"]},
	)


@resume_group.command("list")
@click.pass_context
def resume_list_cmd(ctx):
	"""列出所有本地简历"""
	store = _get_store(ctx)
	items = store.list_all()
	handle_output(
		ctx, "resume", items,
		hints={"next_actions": ["boss resume show <name>", "boss resume init --template default"]},
	)


@resume_group.command("show")
@click.argument("name")
@click.pass_context
def resume_show_cmd(ctx, name):
	"""查看简历详情"""
	store = _get_store(ctx)
	resume = store.get(name)
	if resume is None:
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
		ctx.exit(1)
		return
	handle_output(
		ctx, "resume", resume_to_dict(resume),
		hints={"next_actions": [
			f"boss resume edit {name} --field title --value <新标题>",
			f"boss resume export {name} --format json",
		]},
	)


@resume_group.command("edit")
@click.argument("name")
@click.option("--field", required=True, help="字段路径（如 title, personal_info.layout）")
@click.option("--value", required=True, help="新值")
@click.pass_context
def resume_edit_cmd(ctx, name, field, value):
	"""编辑简历字段"""
	store = _get_store(ctx)
	resume = store.get(name)
	if resume is None:
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
		ctx.exit(1)
		return
	data = resume_to_dict(resume)
	ok = _set_nested(data, field, value)
	if not ok:
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"字段 '{field}' 不存在",
		)
		ctx.exit(1)
		return
	from boss_agent_cli.resume.models import dict_to_resume
	updated = dict_to_resume(data)
	updated.name = name
	store.save(updated)
	handle_output(
		ctx, "resume",
		{"action": "edit", "name": name, "field": field, "value": value},
		hints={"next_actions": [f"boss resume show {name}"]},
	)


@resume_group.command("delete")
@click.argument("name")
@click.pass_context
def resume_delete_cmd(ctx, name):
	"""删除简历"""
	store = _get_store(ctx)
	if not store.exists(name):
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
		ctx.exit(1)
		return
	store.delete(name)
	handle_output(
		ctx, "resume",
		{"action": "delete", "name": name, "deleted": True},
		hints={"next_actions": ["boss resume list"]},
	)


@resume_group.command("export")
@click.argument("name")
@click.option("--format", "fmt", default="json", type=click.Choice(["pdf", "json", "html"]), help="导出格式")
@click.option("-o", "--output", "output_path", default=None, help="输出文件路径")
@click.pass_context
def resume_export_cmd(ctx, name, fmt, output_path):
	"""导出简历"""
	store = _get_store(ctx)
	if not store.exists(name):
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
		ctx.exit(1)
		return
	if fmt in ("pdf", "html"):
		resume = store.get(name)
		if resume is None:
			handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
			ctx.exit(1)
			return
		if output_path is None:
			output_path = f"{name}.{fmt}"
		out = Path(output_path)
		try:
			from boss_agent_cli.resume.export import export_html, export_pdf
			if fmt == "html":
				export_html(resume, out)
			else:
				export_pdf(resume, out)
		except Exception as exc:
			handle_error_output(
				ctx, "resume",
				code="EXPORT_FAILED",
				message=f"{fmt.upper()} 导出失败: {exc}",
				recoverable=True,
				recovery_action="请确认 patchright 已安装并执行 uv run playwright install chromium",
			)
			ctx.exit(1)
			return
		handle_output(
			ctx, "resume",
			{"action": "export", "name": name, "format": fmt, "path": str(out.resolve())},
			hints={"next_actions": [f"boss resume show {name}"]},
		)
		return
	json_str = store.export_json(name)
	export_data = json.loads(json_str)
	if output_path:
		Path(output_path).write_text(json_str, encoding="utf-8")
	handle_output(
		ctx, "resume", export_data,
		hints={"next_actions": [f"boss resume show {name}"]},
	)


@resume_group.command("import")
@click.argument("file_path", type=click.Path())
@click.pass_context
def resume_import_cmd(ctx, file_path):
	"""导入 JSON 简历"""
	path = Path(file_path)
	if not path.exists():
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 不存在",
		)
		ctx.exit(1)
		return
	store = _get_store(ctx)
	try:
		resume = store.import_file(path)
	except (json.JSONDecodeError, KeyError, TypeError) as exc:
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件解析失败: {exc}",
		)
		ctx.exit(1)
		return
	handle_output(
		ctx, "resume",
		{"action": "import", "name": resume.name, "title": resume.title},
		hints={"next_actions": [f"boss resume show {resume.name}"]},
	)


@resume_group.command("clone")
@click.argument("name")
@click.argument("new_name")
@click.pass_context
def resume_clone_cmd(ctx, name, new_name):
	"""复制简历为新版本"""
	store = _get_store(ctx)
	if not store.exists(name):
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name}' 不存在")
		ctx.exit(1)
		return
	if store.exists(new_name):
		handle_error_output(
			ctx, "resume",
			code="RESUME_ALREADY_EXISTS",
			message=f"简历 '{new_name}' 已存在",
			recovery_action="使用不同名称或先删除已有简历",
		)
		ctx.exit(1)
		return
	resume = store.clone(name, new_name)
	handle_output(
		ctx, "resume",
		{"action": "clone", "name": resume.name, "source": name},
		hints={"next_actions": [f"boss resume show {new_name}", f"boss resume diff {name} {new_name}"]},
	)


@resume_group.command("diff")
@click.argument("name1")
@click.argument("name2")
@click.pass_context
def resume_diff_cmd(ctx, name1, name2):
	"""对比两份简历差异"""
	store = _get_store(ctx)
	r1 = store.get(name1)
	if r1 is None:
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name1}' 不存在")
		ctx.exit(1)
		return
	r2 = store.get(name2)
	if r2 is None:
		handle_error_output(ctx, "resume", code="RESUME_NOT_FOUND", message=f"简历 '{name2}' 不存在")
		ctx.exit(1)
		return
	d1 = resume_to_dict(r1)
	d2 = resume_to_dict(r2)
	diffs = _flat_diff(d1, d2)
	handle_output(
		ctx, "resume",
		{"name1": name1, "name2": name2, "diffs": diffs},
		hints={"next_actions": [f"boss resume show {name1}", f"boss resume show {name2}"]},
	)
