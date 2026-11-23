"""CLI 参数入口守卫：UTF-8 可编码性与显式空值。

argv 里的孤立代理项（如从坏 JSON 粘贴出的 ``"\\ud800"``）无法按 UTF-8 编码：
httpx 会在构造 URL / 请求体时抛 ``UnicodeError``，经全局兜底后报成
``LOCAL_IO_ERROR`` + 磁盘指引；某些命令还会在 httpx 失败后降级到浏览器通道，
白起一次浏览器。对确定要发给平台的参数，入口直接按 ``INVALID_PARAM`` 拒绝
（Issue #2533，与 #2499 / #2528 同口径）。

另一族缺陷是显式传入空串 / 纯空白的选项被真值判断吞掉，静默退化成「不筛选」
后才发起搜索甚至写操作（Issue #2598 / #2602）。两类判定都由命令层在构造
client 之前复用。
"""

from __future__ import annotations

from typing import Any

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
	"""显式给出的空白选项按 ``INVALID_PARAM`` 拒绝。

	``None``（未提供）保持原语义；显式空串 / 纯空白不能静默变成「不筛选」，
	否则 Agent 会以为已按条件过滤并据此执行后续写操作（Issue #2598 / #2602）。
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
