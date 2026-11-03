"""筛选项校验失败时的错误信封。

``search`` / ``export`` / ``watch add`` / ``preset add`` / ``batch-greet`` 过去只把
``ValueError`` 的正文塞进 message，``recovery_action`` 落在登记表的「修正参数」，
``hints`` 为 null；Agent 拿到的是一个和正文重复的笼统动作。这里统一补上可执行的
恢复指引：城市错误指向 ``boss cities``，其余筛选项的正文已列出可选值。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.display import boss_command_for_ctx, handle_error_output
from boss_agent_cli.search_filters import UnsupportedCityError


def emit_filter_error(ctx: Any, command: str, exc: ValueError, suffix: str = "") -> None:
	"""按异常类型发出 INVALID_PARAM，并给出修正筛选项的下一步。"""
	message = f"{exc}{suffix}"
	if isinstance(exc, UnsupportedCityError):
		cities_cmd = boss_command_for_ctx(ctx, "cities")
		handle_error_output(
			ctx,
			command,
			code="INVALID_PARAM",
			message=message,
			recovery_action=f"换成 {cities_cmd} 列出的城市名后重试",
			hints={"next_actions": [cities_cmd]},
		)
		return
	handle_error_output(
		ctx,
		command,
		code="INVALID_PARAM",
		message=message,
		recovery_action="按提示把筛选项换成可选值后重试；不确定取值时先不传该筛选项",
	)
