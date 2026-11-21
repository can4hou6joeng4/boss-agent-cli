"""批次 112：boss_agent_train 固定演练的读写判定（Issue #2454）。"""

from __future__ import annotations

from boss_agent_cli import mcp_args, mcp_server


def test_agent_train_argv_never_enables_live() -> None:
	# MCP 映射从不生成 --live：agent train 不传 --live 即演练（本地 CLI 语义）。
	assert mcp_args._build_args("agent_train", {}) == ["agent", "train"]
	assert mcp_args._build_args("agent_train", {"limit": 5}) == ["agent", "train", "--limit", "5"]


def test_agent_train_is_not_treated_as_platform_write() -> None:
	# 超时 / 取消不能报 outcome_unknown：该工具不可能产生平台写入（Issue #2454）。
	assert mcp_server._tool_may_write("boss_agent_train", {}) is False
	assert mcp_server._tool_may_write("boss_agent_train", {"limit": 5}) is False


def test_agent_train_interrupt_recovery_does_not_ask_for_platform_check() -> None:
	recovery = mcp_server._read_interrupt_recovery(("agent", "train", "--limit", "5"))

	assert "演练命令已中断" in recovery
	assert "平台实际结果" not in recovery
	assert "checkpoint" not in recovery


def test_agent_run_still_writes_when_dry_run_disabled() -> None:
	# 反向守卫：真正可写平台的 run 不能被误归为演练。
	assert mcp_server._tool_may_write("boss_agent_run", {"dry_run": False}) is True
	assert mcp_server._read_interrupt_recovery(("agent", "run")) != mcp_server._DRY_RUN_INTERRUPT_RECOVERY
