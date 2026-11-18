"""招聘者命令的后继与恢复提示统一保留当前数据目录。"""

from __future__ import annotations

import click

from boss_agent_cli.display import boss_command_for_ctx, positional_hint, shell_argument

__all__ = ["blank_option_names", "hint_command", "positional_hint", "shell_argument"]


def hint_command(ctx: click.Context, command: str) -> str:
	"""构造招聘者后继命令；``hr`` 组自身会设置 recruiter 角色，无需再带 --role。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def blank_option_names(**values: str | None) -> list[str]:
	"""返回取值为空或纯空白的选项名（``geek_id`` → ``--geek-id``），保持传入顺序。"""
	return [f"--{name.replace('_', '-')}" for name, value in values.items() if value is not None and not value.strip()]
