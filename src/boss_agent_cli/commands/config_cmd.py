"""boss config — 查看和修改配置项。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.browser_source import POLICIES as BROWSER_SOURCES
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.config import DEFAULTS, ROLES, validate_request_config
from boss_agent_cli.crawler.service import crawl_cdp_port, crawl_chrome_path, crawl_int_setting
from boss_agent_cli.display import boss_command_for_ctx, handle_output, render_simple_list
from boss_agent_cli.output import LOG_LEVELS
from boss_agent_cli.platforms import list_platforms
from boss_agent_cli.services.json_files import write_json_atomic

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


@click.group("config", invoke_without_command=True)
@click.pass_context
def config_group(ctx: click.Context) -> None:
	"""查看和修改配置项。不带子命令时显示当前全部配置。"""
	if ctx.invoked_subcommand is None:
		ctx.invoke(config_list_cmd)


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
		is_custom = key in _load_user_overrides(config_path)
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
			boss_command_for_ctx(ctx, "config set <键> <值>"),
			boss_command_for_ctx(ctx, "config get <键>"),
			boss_command_for_ctx(ctx, "config reset <键>"),
		],
	}
	handle_output(
		ctx,
		"config",
		data,
		render=lambda d: render_simple_list(
			d["items"],
			f"配置 ({d['config_path']})",
			[
				("key", "key", "cyan"),
				("value", "value", "green"),
				("source", "source", "dim"),
			],
		),
		hints=hints,
	)


@config_group.command("get")
@click.argument("key")
@click.pass_context
def config_get_cmd(ctx: click.Context, key: str) -> None:
	"""查看单个配置项的值。"""
	cfg = ctx.obj["config"]
	if key not in _PUBLIC_CONFIG_KEYS:
		from boss_agent_cli.output import emit_error

		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项。{_AVAILABLE_CONFIG_MESSAGE}",
		)
		ctx.exit(1)
		return

	data = {
		"key": key,
		"value": cfg.get(key, DEFAULTS[key]),
		"default": DEFAULTS[key],
	}
	handle_output(ctx, "config", data, render=lambda d: None)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set_cmd(ctx: click.Context, key: str, value: str) -> None:
	"""修改配置项。"""
	if key not in _PUBLIC_CONFIG_KEYS:
		from boss_agent_cli.output import emit_error

		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项。{_AVAILABLE_CONFIG_MESSAGE}",
		)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)

	try:
		parsed_value = _parse_value(key, value, DEFAULTS[key])
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
	except (TypeError, ValueError) as exc:
		from boss_agent_cli.output import emit_error

		emit_error("config", code="INVALID_PARAM", message=str(exc))
		ctx.exit(1)
		return
	choices = list_platforms() if key == "platform" else _CONFIG_CHOICES.get(key)
	if choices is not None and parsed_value not in choices:
		from boss_agent_cli.output import emit_error

		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"{key} 必须是以下值之一: {', '.join(choices)}",
		)
		ctx.exit(1)
		return
	user_cfg[key] = parsed_value
	if key == "operating_mode":
		user_cfg.pop("low_risk_mode", None)
	try:
		_save_user_overrides(config_path, user_cfg)
	except OSError as exc:
		_emit_write_error(config_path, exc)
		return

	data = {"key": key, "value": parsed_value, "previous": ctx.obj["config"].get(key)}
	handle_output(ctx, "config", data, render=lambda d: None)


@config_group.command("reset")
@click.argument("key")
@click.pass_context
def config_reset_cmd(ctx: click.Context, key: str) -> None:
	"""将配置项恢复为默认值。"""
	if key not in _PUBLIC_CONFIG_KEYS:
		from boss_agent_cli.output import emit_error

		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项。{_AVAILABLE_CONFIG_MESSAGE}",
		)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)
	user_cfg.pop(key, None)
	try:
		_save_user_overrides(config_path, user_cfg)
	except OSError as exc:
		_emit_write_error(config_path, exc)
		return

	data = {"key": key, "value": DEFAULTS[key], "restored": True}
	handle_output(ctx, "config", data, render=lambda d: None)


def _load_user_overrides(config_path: Path) -> dict[str, Any]:
	"""加载用户自定义配置（不含默认值）。"""
	if config_path.exists():
		try:
			with open(config_path, encoding="utf-8") as f:
				result: dict[str, Any] = json.load(f)
				return result
		except (json.JSONDecodeError, OSError):
			return {}
	return {}


def _save_user_overrides(config_path: Path, user_cfg: dict[str, Any]) -> None:
	"""保存用户配置到文件。"""
	# 原子写：写一半被中断的 config.json 会让所有命令卡在启动阶段。
	write_json_atomic(config_path, user_cfg, trailing_newline=True)


def _emit_write_error(config_path: Path, exc: OSError) -> None:
	"""配置文件写不进去时给信封：目录只读、路径被同名文件占用都不该变成 traceback。"""
	from boss_agent_cli.output import emit_error

	emit_error(
		"config",
		code="LOCAL_IO_ERROR",
		message=f"写入 {config_path} 失败: {exc.strerror or exc}",
	)


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
		except json.JSONDecodeError as exc:
			raise ValueError(
				"结构化配置项必须以 JSON object 传值，例如 '{\"mode\": \"training\"}'"
			) from exc
		if not isinstance(parsed, dict):
			raise ValueError("结构化配置项的值必须是 JSON object")
		return parsed
	if isinstance(default, list):
		parts = raw.split(",")
		if all(isinstance(d, (int, float)) for d in default):
			try:
				return [float(p.strip()) for p in parts]
			except ValueError as exc:
				raise ValueError(f"{key} 必须是逗号分隔的数值列表，例如 '1.5,3.0'，收到 {raw!r}") from exc
		return [p.strip() for p in parts]
	return raw
