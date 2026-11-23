"""招聘者命令的后继与恢复提示统一保留当前数据目录。"""

from __future__ import annotations

import click

from boss_agent_cli.commands._text_guard import blank_option_names, reject_blank_options
from boss_agent_cli.display import boss_command_for_ctx, positional_hint, shell_argument

# 空值守卫已下沉到公共 CLI 支撑模块；这里再导出，招聘者既有调用方保持原样。
__all__ = ["blank_option_names", "hint_command", "positional_hint", "reject_blank_options", "shell_argument"]


def hint_command(ctx: click.Context, command: str) -> str:
	"""构造招聘者后继命令；``hr`` 组自身会设置 recruiter 角色，无需再带 --role。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


