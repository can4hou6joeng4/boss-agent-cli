"""CLI 参数的 UTF-8 可编码性守卫。

argv 里的孤立代理项（如从坏 JSON 粘贴出的 ``"\\ud800"``）无法按 UTF-8 编码：
httpx 会在构造 URL / 请求体时抛 ``UnicodeError``，经全局兜底后报成
``LOCAL_IO_ERROR`` + 磁盘指引；某些命令还会在 httpx 失败后降级到浏览器通道，
白起一次浏览器。对确定要发给平台的参数，入口直接按 ``INVALID_PARAM`` 拒绝
（Issue #2533，与 #2499 / #2528 同口径）。
"""

from __future__ import annotations

import click

from boss_agent_cli.display import handle_error_output
from boss_agent_cli.services.text_files import is_utf8_encodable


def reject_unencodable_params(ctx: click.Context, command: str, /, **fields: str | None) -> bool:
	"""参数含无法按 UTF-8 编码的字符串时发出 ``INVALID_PARAM`` 并返回 True。

	只检查非空字符串：缺省 / 空串由各命令自己的必填校验负责；非字符串值
	（如页码）不在这里处理。
	"""
	bad = [
		name
		for name, value in fields.items()
		if isinstance(value, str) and value and not is_utf8_encodable(value)
	]
	if not bad:
		return False
	labels = " / ".join(bad)
	handle_error_output(
		ctx,
		command,
		code="INVALID_PARAM",
		message=f"{labels} 含有无法按 UTF-8 编码的字符（如孤立代理项），请求未发出",
		recovery_action="去掉无效字符后重试；编号建议重新从搜索结果或 chat 输出中取",
	)
	return True
