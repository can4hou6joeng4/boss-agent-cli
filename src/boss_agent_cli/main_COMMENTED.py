#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[BOSS CLI 主入口] 整个命令行应用的起点
基于 Click 框架构建，提供统一的 CLI 入口点

核心功能：
- 全局参数解析（平台、角色、延迟、CDP 地址等）
- 配置加载与初始化
- 候选者/招聘者命令注册
- 错误处理与 JSON 信封输出
"""

from pathlib import Path
from collections.abc import Sequence
from typing import Any

import click

from boss_agent_cli import __version__
from boss_agent_cli.commands.register import register_candidate_commands, register_recruiter_commands
from boss_agent_cli.config import load_config
from boss_agent_cli.hooks import create_hook_bus
from boss_agent_cli.output import emit_error, Logger
from boss_agent_cli.platforms import list_platforms


class BossCliGroup(click.Group):
	"""
	[自定义 Click Group] 继承自 click.Group，确保 JSON 信封契约在用法错误时也保持一致性
	
	主要作用：
	- 拦截 ClickException 并转换为 JSON 信封输出
	- 保持 CLI 的输出格式一致性（即使报错也返回 JSON）
	"""

	def main(
		self,
		args: Sequence[str] | None = None,
		prog_name: str | None = None,
		complete_var: str | None = None,
		standalone_mode: bool = True,
		**extra: Any,
	) -> Any:
		try:
			# 调用父类 main，但使用 standalone_mode=False 来捕获异常
			return super().main(
				args=args,
				prog_name=prog_name,
				complete_var=complete_var,
				standalone_mode=False,
				**extra,
			)
		except click.ClickException as exc:
			# 如果不是 standalone 模式，重新抛出
			if not standalone_mode:
				raise
			
			# 构建 JSON 信封错误输出
			ctx = getattr(exc, "ctx", None)
			command = getattr(ctx, "info_name", None) or self.name or "boss"
			emit_error(
				command,
				code="INVALID_PARAM",
				message=exc.format_message(),
				recoverable=False,
				recovery_action="修正参数",
			)
			return None


@click.group(name="boss", cls=BossCliGroup, context_settings={"allow_interspersed_args": False})
@click.version_option(version=__version__, prog_name="boss")
@click.option("--data-dir", default="~/.boss-agent", help="数据存储目录，默认 ~/.boss-agent")
@click.option("--delay", default=None, help="请求间隔范围（秒），如 1.5-3.0，用于防止被识别为机器人")
@click.option("--cdp-url", default=None, help="Chrome CDP 调试地址（如 http://localhost:9222），启用则优先用用户 Chrome")
@click.option("--platform", "platform_name", default=None, help="指定招聘平台适配器（默认 zhipin，即 BOSS 直聘）")
@click.option("--role", default=None, type=click.Choice(["candidate", "recruiter"]), help="角色模式：candidate（求职者，默认）/ recruiter（招聘者）")
@click.option("--log-level", default=None, type=click.Choice(["error", "warning", "info", "debug"]), help="日志级别")
@click.option("--json/--no-json", "json_output", default=False, help="强制 JSON 输出（即使在终端中）")
@click.pass_context
def cli(ctx: click.Context, data_dir: str, delay: str | None, cdp_url: str | None, platform_name: str | None, role: str | None, log_level: str | None, json_output: bool) -> None:
	"""
	[CLI 主入口函数] 这是所有 boss 命令的起点
	
	执行流程：
	1. 初始化目录结构
	2. 加载配置
	3. 解析全局参数（延迟、CDP、平台、角色等）
	4. 初始化日志、钩子总线
	5. 注册所有子命令
	"""
	# [1] 确保 Context 对象存在，并设置目录
	ctx.ensure_object(dict)
	resolved_dir = Path(data_dir).expanduser()
	resolved_dir.mkdir(parents=True, exist_ok=True)
	ctx.obj["data_dir"] = resolved_dir
	ctx.obj["json_output"] = json_output

	# [2] 加载配置文件（~/.boss-agent/config.json）
	cfg = load_config(resolved_dir / "config.json")

	# [3] 解析请求延迟范围（用于防风控）
	if delay:
		try:
			low, high = delay.split("-", 1)
			ctx.obj["delay"] = (float(low), float(high))
		except ValueError as exc:
			raise click.BadParameter(
				"delay must be a range like 1.5-3.0",
				param_hint="--delay",
			) from exc
	else:
		# 使用配置文件中的默认值
		ctx.obj["delay"] = tuple(cfg["request_delay"])

	# [4] 初始化日志系统
	level = log_level or cfg["log_level"]
	ctx.obj["log_level"] = level
	ctx.obj["logger"] = Logger(level)
	ctx.obj["cdp_url"] = cdp_url or cfg.get("cdp_url")

	# [5] 解析和验证平台选择
	resolved_platform = platform_name or cfg.get("platform") or "zhipin"
	available = list_platforms()
	if resolved_platform not in available:
		raise click.BadParameter(
			f"unknown platform {resolved_platform!r}, supported: {', '.join(available)}",
			param_hint="--platform",
		)
	ctx.obj["platform"] = resolved_platform

	# [6] 解析和验证角色选择
	resolved_role = role or cfg.get("role") or "candidate"
	ctx.obj["role"] = resolved_role

	# [7] 存储配置和初始化钩子总线
	ctx.obj["config"] = cfg
	ctx.obj["hooks"] = create_hook_bus()


# [8] 注册求职者命令（默认角色）
register_candidate_commands(cli)

# [9] 注册招聘者命令
register_recruiter_commands(cli)


if __name__ == "__main__":
	cli()
