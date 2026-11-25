"""MCP ``boss_me`` 超时估算覆盖默认四段请求（Issue #2852）。

不传 ``--section`` 时 ``commands/me.py`` 依次请求用户信息、简历基本信息、
求职期望和投递记录；每次请求之间都要经过默认节流。缺省 120s 只够一次
请求加一次等待，健康调用会被宿主提前掐断成「底层 CLI 超时」。
"""

from __future__ import annotations

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_cli_timeout,
	_estimated_requests,
)


def test_me_default_estimates_all_four_sections() -> None:
	assert _estimated_requests(("me",)) == 4
	# 4 × 65s 单次请求预算 + 30s 固定余量；必须显著高于一次节流的上界。
	assert _cli_timeout(("me",)) == 4 * 65.0 + 30.0
	assert _cli_timeout(("me",)) > 120.0


def test_me_single_section_keeps_default_timeout() -> None:
	# 两种选项写法都要识别；单段调用只有一次请求，默认 120s 足够。
	for args in (("me", "--section", "deliver"), ("me", "--section=user")):
		assert _estimated_requests(args) == 1
		assert _cli_timeout(args) == 120.0


def test_me_estimate_follows_mcp_mapping() -> None:
	# 映射层不传 section 时生成的就是裸 me，估算必须与生成结果一致。
	assert _build_args("boss_me", {}) == ["me"]
	assert _estimated_requests(tuple(_build_args("boss_me", {}))) == 4
	section_args = _build_args("boss_me", {"section": "deliver"})
	assert _estimated_requests(tuple(section_args)) == 1
	# 投递记录翻页不增加请求次数。
	paged = _build_args("boss_me", {"section": "deliver", "deliver_page": 3})
	assert _estimated_requests(tuple(paged)) == 1
