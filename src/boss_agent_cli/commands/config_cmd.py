"""boss config — 查看和修改配置项。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.display import handle_output, render_simple_list


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
	for key in sorted(DEFAULTS):
		default_val = DEFAULTS[key]
		current_val = cfg.get(key, default_val)
		is_custom = key in _load_user_overrides(config_path)
		items.append({
			"key": key,
			"value": current_val,
			"default": default_val,
			"source": "用户配置" if is_custom else "默认值",
		})

	data = {"config_path": str(config_path), "items": items}
	hints = {
		"next_actions": [
			"boss config set <键> <值> — 修改配置项",
			"boss config get <键> — 查看单个配置项",
			"boss config reset <键> — 恢复默认值",
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
	if key not in DEFAULTS:
		from boss_agent_cli.output import emit_error
		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项: {key}，可用项: {', '.join(sorted(DEFAULTS))}",
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
	if key not in DEFAULTS:
		from boss_agent_cli.output import emit_error
		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项: {key}，可用项: {', '.join(sorted(DEFAULTS))}",
		)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)

	parsed_value = _parse_value(value, DEFAULTS[key])
	user_cfg[key] = parsed_value
	_save_user_overrides(config_path, user_cfg)

	data = {"key": key, "value": parsed_value, "previous": ctx.obj["config"].get(key)}
	handle_output(ctx, "config", data, render=lambda d: None)


@config_group.command("reset")
@click.argument("key")
@click.pass_context
def config_reset_cmd(ctx: click.Context, key: str) -> None:
	"""将配置项恢复为默认值。"""
	if key not in DEFAULTS:
		from boss_agent_cli.output import emit_error
		emit_error(
			"config",
			code="INVALID_PARAM",
			message=f"未知配置项: {key}，可用项: {', '.join(sorted(DEFAULTS))}",
		)
		ctx.exit(1)
		return

	config_path = ctx.obj["data_dir"] / "config.json"
	user_cfg = _load_user_overrides(config_path)
	user_cfg.pop(key, None)
	_save_user_overrides(config_path, user_cfg)

	data = {"key": key, "value": DEFAULTS[key], "restored": True}
	handle_output(ctx, "config", data, render=lambda d: None)


def _load_user_overrides(config_path: Path) -> dict[str, Any]:
	"""加载用户自定义配置（不含默认值）。"""
	if config_path.exists():
		try:
			with open(config_path) as f:
				result: dict[str, Any] = json.load(f)
				return result
		except (json.JSONDecodeError, OSError):
			return {}
	return {}


def _save_user_overrides(config_path: Path, user_cfg: dict[str, Any]) -> None:
	"""保存用户配置到文件。"""
	config_path.parent.mkdir(parents=True, exist_ok=True)
	with open(config_path, "w", encoding="utf-8") as f:
		json.dump(user_cfg, f, ensure_ascii=False, indent=2)
		f.write("\n")


def _parse_value(raw: str, default: Any) -> Any:
	"""根据默认值类型推断并转换输入值。"""
	if default is None:
		if raw.lower() in ("null", "none", ""):
			return None
		return raw
	if isinstance(default, bool):
		return raw.lower() in ("true", "1", "yes")
	if isinstance(default, int):
		return int(raw)
	if isinstance(default, float):
		return float(raw)
	if isinstance(default, list):
		parts = raw.split(",")
		if all(isinstance(d, (int, float)) for d in default):
			return [float(p.strip()) for p in parts]
		return [p.strip() for p in parts]
	return raw
