import json
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import (
	boss_command_for_ctx,
	error_contract_for_code,
	handle_browser_kernel_missing,
	handle_error_output,
	handle_output,
	render_action_result,
	render_list_result,
	render_record_result,
	shell_argument,
)
from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.resume.models import ResumeData, PersonalInfoSection, resume_to_dict
from boss_agent_cli.resume.store import ResumeAlreadyExistsError, ResumeImportError, ResumeStore
from boss_agent_cli.services.browser_runtime import is_browser_kernel_missing
from boss_agent_cli.services.output_path import prepare_output_path


def _get_store(ctx: click.Context) -> ResumeStore:
	resumes_dir: Path = ctx.obj["data_dir"] / "resumes"
	return ResumeStore(resumes_dir)


def _hint_command(ctx: click.Context, command: str) -> str:
	"""简历的后续与恢复命令沿用本地简历所在的数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _require_resume_name(ctx: click.Context, name: str) -> bool:
	"""空简历名按参数错误拒绝。

	空字符串是合法 argv，但拿它查 store 只会得到「简历 '' 不存在」：错误码是
	RESUME_NOT_FOUND（不可恢复、看不出是漏填了名字），而同一类输入在
	`resume init --name ""` / `resume clone <源> ""` 里都是 INVALID_PARAM。
	"""
	if name.strip():
		return True
	list_cmd = _hint_command(ctx, "resume list")
	handle_error_output(
		ctx,
		"resume",
		code="INVALID_PARAM",
		message="简历名称不能为空",
		recovery_action=f"补上简历名称后重试；用 {list_cmd} 查看已有简历",
		hints={"next_actions": [list_cmd]},
	)
	return False


def _emit_resume_not_found(ctx: click.Context, name: str) -> None:
	"""简历不存在：给出列出可选简历的命令，别让 Agent 再猜一次名字。"""
	handle_error_output(
		ctx, "resume",
		code="RESUME_NOT_FOUND",
		message=f"简历 '{name}' 不存在",
		recovery_action=_hint_command(ctx, "resume list"),
		hints={"next_actions": [_hint_command(ctx, "resume list")]},
	)
	ctx.exit(1)


def _load_resume(ctx: click.Context, store: ResumeStore, name: str) -> ResumeData | None:
	"""读取简历；文件存在但读不出来时按「文件损坏」上报，而不是「不存在」。

	以前两者共用 RESUME_NOT_FOUND +「boss resume list」：列表会隐藏坏文件，
	Agent 照提示只能看到一份空列表，再 `resume init` 又被同名文件挡住
	（RESUME_ALREADY_EXISTS），恢复链在这里断掉。
	"""
	resume = store.get(name)
	if resume is not None:
		return resume
	if store.is_corrupt(name):
		handle_error_output(
			ctx,
			"resume",
			code="RESUME_FILE_INVALID",
			message=f"简历文件 '{name}.json' 损坏，无法读取",
			recovery_action=(
				f"修正 resumes/{name}.json 的 JSON 内容后重试，"
				f"或执行 {_hint_command(ctx, f'resume delete {shell_argument(name)}')} 后重新创建；该文件只存在本机"
			),
			hints={
				"next_actions": [
					# 简历名是自由文本（可含空格 / 引号）：不转义时提示会被拆成多个参数。
					_hint_command(ctx, f"resume delete {shell_argument(name)}"),
					_hint_command(ctx, "resume list"),
				]
			},
		)
		ctx.exit(1)
		return None
	_emit_resume_not_found(ctx, name)
	return None


def _emit_export_failure(ctx: click.Context, fmt: str, exc: Exception, *, target: str) -> None:
	"""导出失败的统一出口，按失败原因区分错误码。

	``resume export`` 此前把所有异常都归成 ``EXPORT_FAILED`` +「检查 patchright
	安装」，但 HTML / JSON 导出根本不需要 patchright：目标路径是目录、父目录
	不可写时给的是让用户去装浏览器内核的指引，与真正该做的事（换个路径）无关。
	反过来，PDF 导出命中内核缺失时又把整段 patchright 安装横幅和本机绝对路径
	塞进 ``error.message``。

	三类失败分别处理（与 ``display.handle_auth_errors`` 同一口径）：

	- 内核缺失 → ``BROWSER_KERNEL_MISSING``（确定性失败，重试不会成功）
	- 本地文件读写失败 → ``LOCAL_IO_ERROR``（换个可写路径即可，不是重试平台）
	- 其它（渲染 / 模板错误）→ ``EXPORT_FAILED``，message 里不带本机路径

	``EXPORT_FAILED`` 的登记文案仍指向 patchright 内核（PDF 确实经由浏览器渲染），
	但 HTML / JSON 导出只用本地模板与文件写入，从不启动浏览器：这两类格式的通用
	失败必须换成与浏览器无关的恢复指引（Issue #1031）。
	"""
	if is_browser_kernel_missing(str(exc)):
		handle_browser_kernel_missing(ctx, "resume", exc)
		return
	if is_local_io_error(exc):
		# 目标路径不可写与「导出器坏了」是两回事：前者换路径就能解决，
		# 给个安装浏览器的指引只会让用户白折腾一圈。
		handle_error_output(
			ctx,
			"resume",
			code="LOCAL_IO_ERROR",
			message=f"{fmt.upper()} 导出失败：无法写入 {target}（目标可能是已存在的目录或没有写权限）",
			hints={"operator_actions": ["换一个 --output 路径，或确认目标目录存在且可写后重试"]},
		)
		return
	recoverable, recovery_action = error_contract_for_code("EXPORT_FAILED")
	if fmt != "pdf":
		recovery_action = (
			"检查简历文件内容与导出选项后重试；HTML/JSON 导出只做本地模板渲染，不使用浏览器内核；"
			"仍失败时附上 error.message 与命令提交 issue"
		)
	handle_error_output(
		ctx,
		"resume",
		code="EXPORT_FAILED",
		# 异常文本可能带本机路径或渲染栈：只保留首行，完整信息要 debug 日志。
		message=f"{fmt.upper()} 导出失败: {(str(exc) or type(exc).__name__).splitlines()[0][:300]}",
		recoverable=recoverable,
		recovery_action=recovery_action,
	)


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


def _set_nested(data: dict[str, Any], path: str, value: str) -> bool:
	"""通过 . 分隔路径设置嵌套 dict 字段，返回是否成功"""
	keys = path.split(".")
	current: Any = data
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


def _lookup_nested(data: dict[str, Any], path: str) -> tuple[bool, Any]:
	"""按 . 分隔路径取字段，返回 (是否存在, 当前值)。"""
	current: Any = data
	for key in path.split("."):
		if isinstance(current, dict) and key in current:
			current = current[key]
		else:
			return False, None
	return True, current


def _flat_diff(left: dict[str, Any], right: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
	"""对比两个 dict 的差异，返回 [{field, left, right}, ...]"""
	diffs: list[dict[str, Any]] = []
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
def resume_group() -> None:
	"""本地简历管理。"""


@resume_group.command("init")
@click.option("--name", default=None, help="简历名称")
@click.option("--template", default=None, help="模板名称（如 default）")
@click.pass_context
def resume_init_cmd(ctx: click.Context, name: str | None, template: str | None) -> None:
	"""从默认模板初始化本地简历"""
	store = _get_store(ctx)
	if template is None:
		template = "default"
	if name is None:
		name = "default"
	# 空名称的简历能被写出来，却没法点名：`resume show ` / `resume edit ` 都缺参数，
	# `resume list` 里也只显示一行没有名字的记录。
	if not _require_resume_name(ctx, name):
		return
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
		render=lambda d: render_action_result(
			d, title="resume init", next_steps=[
				_hint_command(ctx, f"resume show {shell_argument(name)}"),
				_hint_command(ctx, f"resume edit {shell_argument(name)} --field title --value <新标题>"),
			]
		),
		hints={"next_actions": [
			_hint_command(ctx, f"resume show {shell_argument(name)}"),
			_hint_command(ctx, f"resume edit {shell_argument(name)} --field title --value <新标题>"),
			_hint_command(ctx, "me"),
			_hint_command(ctx, "resume import <path>"),
		]},
	)


@resume_group.command("list")
@click.pass_context
def resume_list_cmd(ctx: click.Context) -> None:
	"""列出所有本地简历"""
	store = _get_store(ctx)
	items, corrupt = store.list_all_with_corrupt()
	hints: dict[str, Any] = {
		# 空列表里没有简历名可 show：照抄只会换回 RESUME_NOT_FOUND。
		"next_actions": [_hint_command(ctx, "resume init --template default")]
		if not items
		else [_hint_command(ctx, "resume show <name>"), _hint_command(ctx, "resume init --template default")]
	}
	if corrupt:
		# 读不出来的文件不能假装不存在：列表隐藏了它，init 又会被同名文件挡住。
		hints["corrupt"] = corrupt
		hints["operator_actions"] = [
			f"以下简历文件已损坏，先执行 {_hint_command(ctx, 'resume delete <名称>')} 后重新创建，或直接修正 resumes/ 下对应的 .json：{', '.join(corrupt)}"
		]
	handle_output(
		ctx, "resume", items,
		render=lambda d: render_list_result(
			d,
			"resumes",
			[("名称", "name", "bold cyan"), ("标题", "title", "green"), ("更新时间", "updated_at", "dim")],
			next_steps=[_hint_command(ctx, "resume init --template default")]
			if not d
			else [_hint_command(ctx, "resume show <name>"), _hint_command(ctx, "resume init --template default")],
		),
		hints=hints,
	)


@resume_group.command("show")
@click.argument("name")
@click.pass_context
def resume_show_cmd(ctx: click.Context, name: str) -> None:
	"""查看简历详情"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	resume = _load_resume(ctx, store, name)
	if resume is None:
		return
	handle_output(
		ctx, "resume", resume_to_dict(resume),
		render=lambda d: render_record_result(
			d,
			title="resume",
			next_steps=[
				_hint_command(ctx, f"resume edit {shell_argument(name)} --field title --value <新标题>"),
				_hint_command(ctx, f"resume export {shell_argument(name)} --format json"),
			],
		),
		hints={"next_actions": [
			_hint_command(ctx, f"resume edit {shell_argument(name)} --field title --value <新标题>"),
			_hint_command(ctx, f"resume export {shell_argument(name)} --format json"),
		]},
	)


@resume_group.command("edit")
@click.argument("name")
@click.option("--field", required=True, help="字段路径（如 title, personal_info.layout）")
@click.option("--value", required=True, help="新值")
@click.pass_context
def resume_edit_cmd(ctx: click.Context, name: str, field: str, value: str) -> None:
	"""编辑简历字段"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	resume = _load_resume(ctx, store, name)
	if resume is None:
		return
	data = resume_to_dict(resume)
	# --field 只接受文本叶子字段。用路径点到容器（personal_info / modules）或 null
	# （job_intention）时，_set_nested 会把整个子对象换成字符串，dict_to_resume 随后
	# 抛 AttributeError，被兜成不可重试的 INTERNAL_ERROR——参数错误看起来像程序缺陷。
	# name 则会被后面的 updated.name = name 覆盖回去，成功信封与实际内容不符。
	if field == "name":
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"简历名称不能用 --field 修改；先 {_hint_command(ctx, 'resume clone <旧名> <新名>')} 再删除旧简历",
			recovery_action=f"先用 {_hint_command(ctx, 'resume clone <旧名> <新名>')} 复制出改名后的简历，再删除旧简历",
			hints={"next_actions": [_hint_command(ctx, f"resume clone {shell_argument(name)} <新名>")]},
		)
		ctx.exit(1)
		return
	found, current = _lookup_nested(data, field)
	if not found or not isinstance(current, str):
		detail = (
			f"字段 '{field}' 不是文本字段，请点到具体子字段（如 personal_info.layout）"
			if found
			else f"字段 '{field}' 不存在"
		)
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"{detail}；可用 --field title 之类的文本字段，完整结构见 {_hint_command(ctx, f'resume export {shell_argument(name)} --format json')}",
			recovery_action="用 --field 指向存在的文本子字段后重试；先 export 导出 JSON 查看完整结构",
			hints={"next_actions": [_hint_command(ctx, f"resume export {shell_argument(name)} --format json"), _hint_command(ctx, f"resume show {shell_argument(name)}")]},
		)
		ctx.exit(1)
		return
	_set_nested(data, field, value)
	from boss_agent_cli.resume.models import dict_to_resume
	updated = dict_to_resume(data)
	updated.name = name
	store.save(updated)
	handle_output(
		ctx, "resume",
		{"action": "edit", "name": name, "field": field, "value": value},
		render=lambda d: render_action_result(d, title="resume edit", next_steps=[_hint_command(ctx, f"resume show {shell_argument(name)}")]),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(name)}")]},
	)


@resume_group.command("delete")
@click.argument("name")
@click.pass_context
def resume_delete_cmd(ctx: click.Context, name: str) -> None:
	"""删除简历"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	if not store.exists(name):
		_emit_resume_not_found(ctx, name)
		return
	store.delete(name)
	# 关联表以简历名为外键，但没有任何外键约束：不同步清理的话，之后用同一个名字
	# 重建简历（`resume init` 默认就叫 default）会继承旧简历的职位关联，包括已经
	# 标成 applied 的状态和备注，`resume applications` 也就报出这份简历从没关联过的职位。
	removed_links = 0
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed_links = cache.remove_resume_links(name)
	handle_output(
		ctx, "resume",
		{"action": "delete", "name": name, "deleted": True, "removed_links": removed_links},
		render=lambda d: render_action_result(d, title="resume delete", next_steps=[_hint_command(ctx, "resume list")]),
		hints={"next_actions": [_hint_command(ctx, "resume list")]},
	)


@resume_group.command("export")
@click.argument("name")
@click.option("--format", "fmt", default="json", type=click.Choice(["pdf", "json", "html"]), help="导出格式")
@click.option("-o", "--output", "output_path", default=None, help="输出文件路径")
@click.pass_context
def resume_export_cmd(ctx: click.Context, name: str, fmt: str, output_path: str | None) -> None:
	"""导出简历"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	# 文件存在但读不出来（手写坏、字段类型写错）与「不存在」走同一个契约：
	# 其它子命令都是这样。json 导出若交给 export_json 内部的 FileNotFoundError，
	# 会被顶层 OSError 网兜报成 LOCAL_IO_ERROR「本地文件读写失败」，指引用户去查
	# 磁盘与权限，而真正该做的是重建这份简历。
	resume = _load_resume(ctx, store, name)
	if resume is None:
		return
	if fmt in ("pdf", "html"):
		if output_path is None:
			output_path = f"{name}.{fmt}"
		try:
			out = prepare_output_path(output_path)
		except OSError as exc:
			# 建父目录就失败（目标本身是目录、无权限）：与写文件失败同一契约。
			# 这里必须自己出信封：resume_cmd 没有统一异常装饰器，漏出去会落进
			# 顶层 OSError 网兜，被报成「resume 本地文件读写失败」。
			_emit_export_failure(ctx, fmt, exc, target=output_path)
			return
		try:
			from boss_agent_cli.resume.export import export_html, export_pdf
			if fmt == "html":
				export_html(resume, out)
			else:
				export_pdf(resume, out)
		except Exception as exc:
			_emit_export_failure(ctx, fmt, exc, target=str(out))
			return
		handle_output(
			ctx, "resume",
			{"action": "export", "name": name, "format": fmt, "path": str(out.resolve())},
			render=lambda d: render_action_result(d, title="resume export", next_steps=[f"open {shell_argument(str(out.resolve()))}"]),
			hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(name)}")]},
		)
		return
	json_str = store.export_json(name)
	export_data = json.loads(json_str)
	if output_path:
		try:
			prepare_output_path(output_path).write_text(json_str, encoding="utf-8")
		except OSError as exc:
			_emit_export_failure(ctx, fmt, exc, target=output_path)
			return
	handle_output(
		ctx, "resume", export_data,
		render=lambda d: render_record_result(
			d, title="resume export", next_steps=[_hint_command(ctx, f"resume show {shell_argument(name)}")]
		),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(name)}")]},
	)


@resume_group.command("import")
@click.argument("file_path", type=click.Path())
@click.option("--force", is_flag=True, help="覆盖同名简历（会丢弃本地已有的编辑）")
@click.pass_context
def resume_import_cmd(ctx: click.Context, file_path: Path, force: bool) -> None:
	"""导入 JSON 简历"""
	# 空路径会被 click.Path() 解析成当前目录（Path("") == "."），既存在又是目录，
	# 于是绕过「文件不存在」检查，在打开文件时抛 IsADirectoryError，被顶层兜成
	# LOCAL_IO_ERROR「本地文件读写失败」——参数少写一个值看起来像磁盘或权限问题。
	if not str(file_path).strip():
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message="文件路径不能为空",
			recovery_action=_hint_command(ctx, "resume import <简历JSON路径>"),
			hints={"next_actions": [_hint_command(ctx, "resume import <file>"), _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return
	path = Path(file_path)
	if path.is_dir():
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 是目录，不是简历 JSON 文件",
			recovery_action=_hint_command(ctx, "resume import <简历JSON路径>"),
			hints={"next_actions": [_hint_command(ctx, "resume import <file>"), _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return
	if not path.exists():
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 不存在",
			recovery_action=_hint_command(ctx, "resume import <简历JSON路径>"),
			hints={"next_actions": [_hint_command(ctx, "resume import <file>"), _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return
	store = _get_store(ctx)
	try:
		resume = store.import_file(path, overwrite=force)
	except ResumeAlreadyExistsError as exc:
		handle_error_output(
			ctx, "resume",
			code="RESUME_ALREADY_EXISTS",
			message=f"简历 '{exc.name}' 已存在",
			recovery_action="修改文件里的 name 字段后重试，或加 --force 覆盖（会丢弃本地已有的编辑）",
		)
		ctx.exit(1)
		return
	except ResumeImportError as exc:
		export_cmd = _hint_command(ctx, "resume export <简历> --format json")
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 导入失败: {exc}",
			recovery_action=f"按提示修正 JSON 文件（UTF-8 编码、合法 JSON、顶层对象、非空 name 字段）后重试；可用 {export_cmd} 对照结构",
			hints={"next_actions": [export_cmd, _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return
	except (KeyError, TypeError, AttributeError, ValueError) as exc:
		# 结构不对的 JSON（字段类型写错、嵌套层不是对象）只在模型解析时暴露：
		# 这是用户输入边界，兜底成 INVALID_PARAM，而不是把 traceback 甩出去。
		handle_error_output(
			ctx, "resume",
			code="INVALID_PARAM",
			message=f"文件 '{file_path}' 解析失败: {exc}",
			recovery_action=f"按提示修正 JSON 字段后重试；可用 {_hint_command(ctx, 'resume export <简历> --format json')} 对照结构",
			hints={"next_actions": [_hint_command(ctx, "resume export <简历> --format json"), _hint_command(ctx, "resume list")]},
		)
		ctx.exit(1)
		return
	handle_output(
		ctx, "resume",
		{"action": "import", "name": resume.name, "title": resume.title},
		render=lambda d: render_action_result(d, title="resume import", next_steps=[_hint_command(ctx, f"resume show {shell_argument(resume.name)}")]),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(resume.name)}")]},
	)


@resume_group.command("clone")
@click.argument("name")
@click.argument("new_name")
@click.pass_context
def resume_clone_cmd(ctx: click.Context, name: str, new_name: str) -> None:
	"""复制简历为新版本"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	# 空新名称会复制出一份无法点名的简历，`resume show ` 缺参数。
	if not new_name.strip():
		handle_error_output(
			ctx,
			"resume",
			code="INVALID_PARAM",
			message="新简历名称不能为空",
			recovery_action=f"补上新简历名称后重试，例如 {_hint_command(ctx, 'resume clone <源简历> <新名称>')}",
			hints={"next_actions": [_hint_command(ctx, "resume list")]},
		)
		return
	if _load_resume(ctx, store, name) is None:
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
		render=lambda d: render_action_result(
			d, title="resume clone", next_steps=[_hint_command(ctx, f"resume show {shell_argument(new_name)}"), _hint_command(ctx, f"resume diff {shell_argument(name)} {shell_argument(new_name)}")]
		),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(new_name)}"), _hint_command(ctx, f"resume diff {shell_argument(name)} {shell_argument(new_name)}")]},
	)


@resume_group.command("diff")
@click.argument("name1")
@click.argument("name2")
@click.pass_context
def resume_diff_cmd(ctx: click.Context, name1: str, name2: str) -> None:
	"""对比两份简历差异"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name1):
		return
	if not _require_resume_name(ctx, name2):
		return
	r1 = _load_resume(ctx, store, name1)
	if r1 is None:
		return
	r2 = _load_resume(ctx, store, name2)
	if r2 is None:
		return
	d1 = resume_to_dict(r1)
	d2 = resume_to_dict(r2)
	diffs = _flat_diff(d1, d2)
	handle_output(
		ctx, "resume",
		{"name1": name1, "name2": name2, "diffs": diffs},
		render=lambda d: render_list_result(
			d.get("diffs", []),
			f"diff: {name1} ↔ {name2}",
			[("字段", "field", "bold cyan"), (name1, "left", "green"), (name2, "right", "yellow")],
			next_steps=[_hint_command(ctx, f"resume show {shell_argument(name1)}"), _hint_command(ctx, f"resume show {shell_argument(name2)}")],
		),
		hints={"next_actions": [_hint_command(ctx, f"resume show {shell_argument(name1)}"), _hint_command(ctx, f"resume show {shell_argument(name2)}")]},
	)


@resume_group.command("link")
@click.argument("name")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--title", default="", help="职位名称")
@click.option("--company", default="", help="公司名称")
@click.pass_context
def resume_link_cmd(ctx: click.Context, name: str, security_id: str, job_id: str, title: str, company: str) -> None:
	"""关联简历与职位"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	if not store.exists(name):
		_emit_resume_not_found(ctx, name)
		return
	# 空字符串仍是合法 argv，但不构成一条职位关联：存进去之后 `resume applications`
	# 会多出一行空 ID，提示里的 `boss apply  ` 也没有可执行参数。
	if not security_id.strip() or not job_id.strip():
		search_cmd = _hint_command(ctx, "search <关键词>")
		handle_error_output(
			ctx,
			"resume",
			code="INVALID_PARAM",
			message="security_id 与 job_id 不能为空",
			recovery_action=f"先用 {search_cmd} 取到完整的职位参数，再关联到简历",
			hints={"next_actions": [search_cmd]},
		)
		return
	cache = CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db")
	cache.link_resume_to_job(name, security_id, job_id, title, company)
	cache.close()
	handle_output(
		ctx, "resume",
		{"action": "link", "name": name, "security_id": security_id, "job_id": job_id},
		render=lambda d: render_action_result(
			d, title="resume link", next_steps=[_hint_command(ctx, f"resume applications {shell_argument(name)}"), _hint_command(ctx, f"apply {shell_argument(security_id)} {shell_argument(job_id)}")]
		),
		hints={"next_actions": [_hint_command(ctx, f"resume applications {shell_argument(name)}"), _hint_command(ctx, f"apply {shell_argument(security_id)} {shell_argument(job_id)}")]},
	)


@resume_group.command("applications")
@click.argument("name")
@click.pass_context
def resume_applications_cmd(ctx: click.Context, name: str) -> None:
	"""查看简历关联的所有职位"""
	store = _get_store(ctx)
	if not _require_resume_name(ctx, name):
		return
	if not store.exists(name):
		_emit_resume_not_found(ctx, name)
		return
	cache = CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db")
	items = cache.get_resume_applications(name)
	cache.close()
	handle_output(
		ctx, "resume",
		items,
		render=lambda d: render_list_result(
			d,
			f"{name} 关联职位",
			[("职位", "job_title", "bold cyan"), ("公司", "company", "green"), ("状态", "status", "yellow"), ("更新", "updated_at", "dim")],
			next_steps=[_hint_command(ctx, f"resume link {shell_argument(name)} <security_id> <job_id>")] if not d else [_hint_command(ctx, f"resume show {shell_argument(name)}")],
		),
		# 与 TTY 同一口径：没有关联职位时 show 只能看到简历本身，下一步是关联一个职位。
		hints={"next_actions": [_hint_command(ctx, f"resume link {shell_argument(name)} <security_id> <job_id>")]
		if not items
		else [_hint_command(ctx, f"resume show {shell_argument(name)}")]},
	)
