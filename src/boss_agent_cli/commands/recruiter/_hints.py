"""招聘者命令的后继与恢复提示统一保留当前数据目录。"""

from __future__ import annotations

import click

from boss_agent_cli.display import boss_command_for_ctx


def hint_command(ctx: click.Context, command: str) -> str:
	"""构造招聘者后继命令；``hr`` 组自身会设置 recruiter 角色，无需再带 --role。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)
