"""招聘者命令的后继与恢复提示统一保留当前数据目录。"""

from __future__ import annotations

import click

from boss_agent_cli.commands._text_guard import blank_option_names, reject_blank_options
from boss_agent_cli.display import boss_command_for_ctx, positional_hint, shell_argument

# 空值守卫已下沉到公共 CLI 支撑模块；这里再导出，招聘者既有调用方保持原样。
__all__ = ["blank_option_names", "hint_command", "positional_hint", "reject_blank_options", "shell_argument"]


def hint_command(ctx: click.Context, command: str, *, role: str | None = None) -> str:
	"""构造招聘者后继命令；``hr`` 组自身会设置 recruiter 角色。

	``role`` 用于不属于 ``hr`` 组的后继命令（如 ``agent run --resume``）：
	新进程不会继承当前角色，不带 ``--role recruiter`` 会按配置或默认候选人
	角色读取另一套本地状态（Issue #3058）。
	"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True, role=role)
