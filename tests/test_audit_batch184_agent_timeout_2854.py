"""MCP 招聘自动化的超时估算（Issue #2854）。

一轮 ``agent run`` / ``agent train`` 先按 tab 扫描，再逐会话读取历史并在需要时
派发一次动作；默认 automation 配置下会话数远超 120s 宿主超时能覆盖的请求量。
"""

from __future__ import annotations

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_MAX_ESTIMATED_REQUESTS,
	_cli_timeout,
	_estimated_requests,
)


def test_agent_without_limit_uses_maximum_timeout() -> None:
	# 缺省 --limit 时扫描量由本地 automation 配置决定；按封顶值给满。
	for args in (("agent", "run", "--dry-run"), ("agent", "train")):
		assert _estimated_requests(args) == _MAX_ESTIMATED_REQUESTS
		assert _cli_timeout(args) == 900.0


def test_agent_limit_counts_scan_and_per_conversation_requests() -> None:
	# 两个 tab 扫描 + 每个会话一次读取 + 至多一次动作。
	for args in (("agent", "run", "--limit", "2"), ("agent", "train", "--limit", "2")):
		assert _estimated_requests(args) == 6
		assert _cli_timeout(args) == 6 * 65.0 + 30.0


def test_agent_invalid_limit_keeps_default_timeout() -> None:
	# CLI 的 IntRange(min=1) 会在发出请求前拒绝；非法值不需要放大超时。
	for value in ("0", "-1", "not-a-number"):
		args = ("agent", "run", "--limit", value)
		assert _estimated_requests(args) is None
		assert _cli_timeout(args) == 120.0


def test_agent_estimate_follows_mcp_mapping() -> None:
	assert _build_args("boss_agent_run", {}) == ["agent", "run", "--dry-run"]
	assert _estimated_requests(tuple(_build_args("boss_agent_run", {}))) == _MAX_ESTIMATED_REQUESTS
	live = _build_args("boss_agent_run", {"dry_run": False})
	assert _estimated_requests(tuple(live)) == _MAX_ESTIMATED_REQUESTS
	assert _estimated_requests(tuple(_build_args("boss_agent_run", {"limit": 2}))) == 6
	assert _estimated_requests(tuple(_build_args("boss_agent_train", {"limit": 2}))) == 6
	assert _estimated_requests(tuple(_build_args("boss_agent_train", {}))) == _MAX_ESTIMATED_REQUESTS
