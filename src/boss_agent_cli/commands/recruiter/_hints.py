"""招聘者命令的后继与恢复提示统一保留当前数据目录。"""

from __future__ import annotations

from typing import Any

import click

from boss_agent_cli.display import boss_command_for_ctx, handle_error_output, positional_hint, shell_argument

__all__ = ["blank_option_names", "hint_command", "positional_hint", "reject_blank_options", "shell_argument"]


def hint_command(ctx: click.Context, command: str) -> str:
	"""构造招聘者后继命令；``hr`` 组自身会设置 recruiter 角色，无需再带 --role。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def blank_option_names(**values: Any) -> list[str]:
	"""返回取值为空、纯空白或非字符串的选项名（``geek_id`` → ``--geek-id``），保持传入顺序。

	非字符串值（如 MCP / 直接调用传进来的 ``123``）不能直接 ``.strip()``：
	那会抛 ``AttributeError`` 落成 ``INTERNAL_ERROR``（Issue #1841）；按非法
	取值处理，与空串同口径返回选项名。
	"""
	return [
		f"--{name.replace('_', '-')}"
		for name, value in values.items()
		if value is not None and not (isinstance(value, str) and value.strip())
	]


def reject_blank_options(ctx: click.Context, command: str, /, **values: Any) -> bool:
	"""显式给出的空白筛选项按 ``INVALID_PARAM`` 拒绝。

	``None``（未提供）保持原语义；显式空串 / 纯空白不能静默变成「不筛选」，
	否则 Agent 会以为已按职位 / 城市过滤并据此执行后续写操作（Issue #2598）。
	"""
	blank = blank_option_names(**values)
	if not blank:
		return False
	labels = " / ".join(blank)
	handle_error_output(
		ctx,
		command,
		code="INVALID_PARAM",
		message=f"{labels} 不能为空",
		recovery_action=f"补上 {labels} 的有效值后重试；若不按该条件筛选，请省略该选项",
	)
	return True
