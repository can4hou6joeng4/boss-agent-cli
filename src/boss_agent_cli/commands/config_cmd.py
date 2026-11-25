"""boss config — 查看和修改配置项。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.config import CDP_URL_REQUIREMENT, DEFAULTS, ROLES, is_valid_cdp_url, validate_request_config
from boss_agent_cli.crawler.service import crawl_cdp_port, crawl_chrome_path, crawl_int_setting
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_error_output,
	handle_output,
	render_action_result,
	render_simple_list,
)
from boss_agent_cli.output import LOG_LEVELS
from boss_agent_cli.platforms import list_platforms
from boss_agent_cli.services.json_files import read_bytes_if_regular, write_json_atomic

_CONFIG_CHOICES = {
	"operating_mode": ("assisted", "research"),
	"browser_source": tuple(BROWSER_SOURCES),
	# 启动期会拒绝取值域之外的 role；写入时不校验的话，`config set role admin`
	# 会报成功，之后每条命令（连 config reset 也一样）都在启动阶段失败。
	"role": ROLES,
	"log_level": LOG_LEVELS,
}
_PUBLIC_CONFIG_KEYS = tuple(key for key in DEFAULTS if key != "low_risk_mode")
_AVAILABLE_CONFIG_MESSAGE = f"可用项: {', '.join(sorted(_PUBLIC_CONFIG_KEYS))}"
_STRUCTURED_PLACEHOLDER = "（JSON，见下方）"


def _config_value_text(value: Any) -> Any:
	"""配置值在终端里的写法：未设置项与列表不走 Python repr（Issue #2755）。

	- ``None``（未设置的 ``cdp_url`` / ``export_dir`` / ``request_budget_dir``）显示
	  「（未设置）」，而不是字符串 ``None``；
	- 列表按 JSON 数组显示（``[30.0, 60.0]``），与 ``--json`` 信封、文档默认值表
	  一致，照抄就能写回 ``config set``；
	- 对象值仍占位，由调用方在表格 / 面板下方展开完整 JSON。
	"""
	if value is None:
		return "（未设置）"
	if isinstance(value, dict):
		return _STRUCTURED_PLACEHOLDER
	if isinstance(value, list):
		try:
			return json.dumps(value, ensure_ascii=False, default=str)
		except (TypeError, ValueError, RecursionError):
			return str(value)
	return value


def _hint_command(ctx: click.Context, command: str) -> str:
	"""配置读写与修复命令沿用当前数据目录中的 config.json。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _emit_unknown_key(ctx: click.Context) -> None:
	"""未知配置项：消息里列出可用项，hints 给出能看到当前值的 config list。"""
	handle_error_output(
		ctx,
		"config",
		code="INVALID_PARAM",
		message=f"未知配置项。{_AVAILABLE_CONFIG_MESSAGE}",
		hints={"next_actions": [_hint_command(ctx, "config list")]},
	)


def _emit_invalid_value(ctx: click.Context, key: str, message: str) -> None:
	"""取值不合法：消息已说明原因，再给出查看当前值与可用项的下一步。

	``config set log_level bogus`` / ``config set request_delay abc`` 原来只有中文
	message，``recovery_action`` 是笼统的「修正参数」、``hints`` 为 null；Agent 拿不到
	结构化的 ``config get <键>`` / ``config list``。
	"""
	handle_error_output(
		ctx,
		"config",
		code="INVALID_PARAM",
		message=message,
		recovery_action=f"按提示修正 {key} 的取值；用 {_hint_command(ctx, 'config list')} 查看当前值与可用项",
		hints={
			"next_actions": [
				_hint_command(ctx, f"config get {key}"),
				_hint_command(ctx, "config list"),
			]
		},
	)


@click.group("config", invoke_without_command=True)
@click.pass_context
def config_group(ctx: click.Context) -> None:
	"""查看和修改配置项。不带子命令时显示当前全部配置。"""
	if ctx.invoked_subcommand is None:
		ctx.invoke(config_list_cmd)


def _render_config_list(data: dict[str, Any]) -> None:
	"""TTY 下的配置列表：结构化配置在表格里占位，完整 JSON 放到表格下方。

	``automation`` 的字典 repr 有二十多行，展开在单元格里会挤满整张表格——用户
	反而看不到自己配的内容。
	"""
	items: list[dict[str, Any]] = data["items"]
	structured = {item["key"]: item["value"] for item in items if isinstance(item["value"], dict)}
	rows = [{**item, "value": _config_value_text(item["value"])} for item in items]
	render_simple_list(
		rows,
		f"配置 ({data['config_path']})",
		[
			("key", "key", "cyan"),
			("value", "value", "green"),
			("source", "source", "dim"),
		],
	)
	if not structured:
		return
	from boss_agent_cli import display

	for key, value in structured.items():
		display.console.print()
		display.console.print(f"[bold]{key}[/bold]")
		display.console.print_json(json.dumps({key: value}, ensure_ascii=False, sort_keys=True))


@config_group.command("list")
@click.pass_context
def config_list_cmd(ctx: click.Context) -> None:
	"""显示当前全部配置。"""
	cfg = ctx.obj["config"]
	config_path = ctx.obj["data_dir"] / "config.json"

	items = []
	for key in sorted(_PUBLIC_CONFIG_KEYS):
		default_val = DEFAULTS[key]
		current_val = cfg.get(key, default_val)
		overrides = _load_user_overrides(config_path)
		# 旧版的 low_risk_mode 会派生 operating_mode，只看新键会把非默认值
		# 标成「默认值」（Issue #1823）。
		is_custom = key in overrides or (key == "operating_mode" and "low_risk_mode" in overrides)
		items.append(
			{
				"key": key,
				"value": current_val,
				"default": default_val,
				"source": "用户配置" if is_custom else "默认值",
			}
		)

	data = {"config_path": str(config_path), "items": items}
	hints = {
		"next_actions": [
			_hint_command(ctx, "config set <键> <值>"),
			_hint_command(ctx, "config get <键>"),
			_hint_command(ctx, "config reset <键>"),
		],
	}
	handle_output(
		ctx,
		"config",
		data,
		render=_render_config_list,
		hints=hints,
	)


def _render_config_result(data: dict[str, Any], *, title: str, next_actions: list[str]) -> None:
	"""``config get/set/reset`` 的 TTY 渲染：结构化值改用格式化 JSON。

	``automation`` 的 repr 把两段十一字段字典挤在一个面板里，单引号与折行让
	「当前值」和「默认值 / 上一次的值」很难读，也难以对比。
	"""
	structured = {k: v for k, v in data.items() if isinstance(v, dict)}
	flat = {key: _config_value_text(value) for key, value in data.items()}
	render_action_result(flat, title=title, next_steps=next_actions)
	if not structured:
		return
	payload: dict[str, Any] = {}
	for key in ("value", "default", "previous"):
		if key not in structured:
			continue
		# 与当前值相同的字段只展开一次：默认值场景下重复打印两份相同 JSON 没有信息量。
		if key == "value" or structured[key] != structured.get("value"):
			payload[key] = structured[key]
	from boss_agent_cli import display

	display.console.print()
	display.console.print_json(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@config_group.command("get")
@click.argument("key")
@click.pass_context
def config_get_cmd(ctx: click.Context, key: str) -> None:
	"""查看单个配置项的值。"""
	cfg = ctx.obj["config"]
	if key not in _PUBLIC_CONFIG_KEYS:
		_emit_unknown_key(ctx)
		ctx.exit(1)
		return

	data = {
		"key": key,
		"value": cfg.get(key, DEFAULTS[key]),
		"default": DEFAULTS[key],
	}
	# `render=lambda d: None` 让 TTY 下的 get / set / reset 什么都不打印：真人在终端
	# 改完配置看不到任何确认，只能再跑一次 config list 才能确认是否生效。
	next_actions = [
		_hint_command(ctx, f"config set {key} <值>"),
		_hint_command(ctx, "config list"),
	]
	handle_output(
		ctx,
		"config",
		data,
		render=lambda d: _render_config_result(d, title="config get", next_actions=next_actions),
		hints={"next_actions": next_actions},
	)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set_cmd(ctx: click.Context, key: str, value: str) -> None:
	"""修改配置项。"""
	if key not in _PUBLIC_CONFIG_KEYS:
		_emit_unknown_key(ctx)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)

	try:
		parsed_value = _parse_value(key, value, DEFAULTS[key])
		_ensure_encodable_value(key, parsed_value)
		if key == "automation":
			# config.json 是 agent run/train 的直接输入，先用正式解析器校验内容，
			# 避免把非法枚举或数值写进去后，运行时才在依赖内部抛 traceback。
			try:
				automation_config_from_dict(parsed_value)
			except (TypeError, ValueError) as exc:
				raise ValueError(f"automation 配置无效: {exc}") from exc
		if key == "crawl":
			# 与 crawl run/start 和向导采集共用同一组校验：注定被拒绝的值在写入时
			# 就报错，而不是落盘后每次运行都返回 INVALID_PARAM。
			try:
				crawl_int_setting(parsed_value, "max_requests", 20)
				crawl_int_setting(parsed_value, "max_details", 50)
				crawl_int_setting(parsed_value, "max_seconds", 600)
				crawl_int_setting(parsed_value, "max_retries", 1, minimum=0)
				crawl_chrome_path(parsed_value.get("chrome_path"))
				raw_port = parsed_value.get("cdp_port")
				if raw_port is not None:
					crawl_cdp_port(raw_port)
			except (TypeError, ValueError) as exc:
				raise ValueError(f"crawl 配置无效: {exc}") from exc
		if key.startswith("request_") or key == "batch_greet_delay":
			# Validate the edited field without preventing repairs of other bad fields.
			validate_request_config({**DEFAULTS, key: parsed_value})
		if key == "cdp_url" and parsed_value is not None and not is_valid_cdp_url(parsed_value):
			# 启动期按同一规则拒绝坏地址：写进去会让之后每条命令都启动失败，
			# 连 `config get` 也只能靠 config 子命令的豁免勉强可用。
			raise ValueError(f"{CDP_URL_REQUIREMENT}，收到 {parsed_value!r}")
	except (TypeError, ValueError) as exc:
		_emit_invalid_value(ctx, key, str(exc))
		ctx.exit(1)
		return
	choices = list_platforms() if key == "platform" else _CONFIG_CHOICES.get(key)
	if choices is not None and parsed_value not in choices:
		_emit_invalid_value(ctx, key, f"{key} 必须是以下值之一: {', '.join(choices)}")
		ctx.exit(1)
		return
	user_cfg[key] = parsed_value
	if key == "operating_mode":
		user_cfg.pop("low_risk_mode", None)
	try:
		_save_user_overrides(config_path, user_cfg)
	except OSError as exc:
		_emit_write_error(ctx, config_path, exc)
		return

	data = {"key": key, "value": parsed_value, "previous": ctx.obj["config"].get(key)}
	next_actions = [
		_hint_command(ctx, f"config get {key}"),
		_hint_command(ctx, "config list"),
	]
	handle_output(
		ctx,
		"config",
		data,
		render=lambda d: _render_config_result(d, title="config set", next_actions=next_actions),
		hints={"next_actions": next_actions},
	)


@config_group.command("reset")
@click.argument("key")
@click.pass_context
def config_reset_cmd(ctx: click.Context, key: str) -> None:
	"""将配置项恢复为默认值。"""
	if key not in _PUBLIC_CONFIG_KEYS:
		_emit_unknown_key(ctx)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)
	user_cfg.pop(key, None)
	if key == "operating_mode":
		# 只删新键时旧版 low_risk_mode 会让生效值继续保持 research：
		# reset 报成功、下一步 config get 却证伪（Issue #1823）。
		user_cfg.pop("low_risk_mode", None)
	try:
		_save_user_overrides(config_path, user_cfg)
	except OSError as exc:
		_emit_write_error(ctx, config_path, exc)
		return

	data = {"key": key, "value": DEFAULTS[key], "restored": True}
	next_actions = [
		_hint_command(ctx, f"config get {key}"),
		_hint_command(ctx, "config list"),
	]
	handle_output(
		ctx,
		"config",
		data,
		render=lambda d: _render_config_result(d, title="config reset", next_actions=next_actions),
		hints={"next_actions": next_actions},
	)


def _load_user_overrides(config_path: Path) -> dict[str, Any]:
	"""加载用户自定义配置（不含默认值）。

	只有文件确实不存在或 JSON 语法损坏才返回空配置。路径状态读不出来时
	上抛 ``OSError``：否则随后的整文件写回会丢掉其他配置项（Issue #1695）。
	"""
	try:
		raw = read_bytes_if_regular(config_path)
		if raw is None:
			return {}
		result = json.loads(raw)
	# 超长整数（3.11+）与深嵌套同样按配置损坏降级，不影响 config 子命令。
	except (ValueError, RecursionError):
		return {}
	if not isinstance(result, dict):
		return {}
	return result


def _save_user_overrides(config_path: Path, user_cfg: dict[str, Any]) -> None:
	"""保存用户配置到文件。"""
	# 原子写：写一半被中断的 config.json 会让所有命令卡在启动阶段。
	write_json_atomic(config_path, user_cfg, trailing_newline=True)


def _emit_write_error(ctx: click.Context, config_path: Path, exc: OSError) -> None:
	"""配置文件写不进去时给信封：目录只读、路径被同名文件占用都不该变成 traceback。"""
	handle_error_output(
		ctx,
		"config",
		code="LOCAL_IO_ERROR",
		message=f"写入 {config_path} 失败: {exc.strerror or exc}",
	)


def _ensure_encodable_value(key: str, value: Any) -> None:
	"""拒绝无法按 UTF-8 编码的孤立代理字符（Issue #1294）。

	argv 里的非法字节会被 Python 解码成 U+DCFF 之类的孤立代理；写进
	config.json 后，之后每次读配置（config list、启动期加载）都可能让
	输出信封无法编码、stdout 丢掉整个 JSON。在输入边界直接报
	INVALID_PARAM，而不是静默写入一份读出来就崩的配置。
	"""
	try:
		json.dumps(value, ensure_ascii=False).encode("utf-8")
	except UnicodeEncodeError as exc:
		raise ValueError(f"{key} 含无法按 UTF-8 编码的字符（{exc.reason}）") from exc


def _parse_value(key: str, raw: str, default: Any) -> Any:
	"""根据默认值类型推断并转换输入值。

	转换失败要说明是哪个配置项、收到的是什么：裸的 ``int(raw)`` / ``float(raw)``
	只会把 ``could not convert string to float: 'abc'`` 这种英文原文抛给用户。
	"""
	if default is None:
		if raw.lower() in ("null", "none", ""):
			return None
		return raw
	if isinstance(default, bool):
		return raw.lower() in ("true", "1", "yes")
	if isinstance(default, int):
		try:
			return int(raw)
		except ValueError as exc:
			raise ValueError(f"{key} 必须是整数，收到 {raw!r}") from exc
	if isinstance(default, float):
		try:
			return float(raw)
		except ValueError as exc:
			raise ValueError(f"{key} 必须是数值，收到 {raw!r}") from exc
	if isinstance(default, dict):
		# automation / crawl 这类结构化配置不是标量：直接存字符串会让后续读取方
		# （如 automation_config_from_dict）在 .get() 上崩溃。这里只接受 JSON object。
		try:
			parsed = json.loads(raw)
		# 超长整数与深嵌套和语法错误一样：都是用户传入的值无法解析。
		except (ValueError, RecursionError) as exc:
			raise ValueError(
				"结构化配置项必须以 JSON object 传值，例如 '{\"mode\": \"training\"}'"
			) from exc
		if not isinstance(parsed, dict):
			raise ValueError("结构化配置项的值必须是 JSON object")
		return parsed
	if isinstance(default, list):
		if all(isinstance(d, (int, float)) for d in default):
			# `config get` / `config list` 与文档展示的都是数组写法 `[30.0, 60.0]`：
			# 读到什么就要能写回什么，只认逗号写法会让原样回写变成 INVALID_PARAM
			# （Issue #2757）。两种写法等价，元素个数与取值范围仍由后续校验把关。
			text = raw.strip()
			if text.startswith("[") and text.endswith("]"):
				text = text[1:-1]
			try:
				return [float(part.strip()) for part in text.split(",")]
			except ValueError as exc:
				# 示例取该配置项自己的默认值：不会给出低于 doctor 告警线的间隔。
				example = [f"{number:g}" for number in default]
				raise ValueError(
					f"{key} 必须是逗号分隔的数值列表，例如 '{','.join(example)}' 或 '[{', '.join(example)}]'，收到 {raw!r}"
				) from exc
		return [p.strip() for p in raw.split(",")]
	return raw
