"""第二十轮审计批次：boss_agent_run 省略 dry_run 时的读写判定（Issue #2414）。"""

from __future__ import annotations

from boss_agent_cli import mcp_args, mcp_server
from boss_agent_cli.mcp_tools import TOOLS


def test_agent_run_omitted_dry_run_is_read_only() -> None:
	# 省略键、显式 true 与只带其他参数都等于 schema 默认的 --dry-run：
	# 超时 / 取消不能报 outcome_unknown（Issue #2414）。
	assert mcp_server._tool_may_write("boss_agent_run", {}) is False
	assert mcp_server._tool_may_write("boss_agent_run", {"limit": 5}) is False
	assert mcp_server._tool_may_write("boss_agent_run", {"dry_run": True}) is False
	# 显式关闭预览才产生平台写入。
	assert mcp_server._tool_may_write("boss_agent_run", {"dry_run": False}) is True


def test_agent_run_omitted_dry_run_builds_dry_run_argv() -> None:
	assert mcp_args._build_args("agent_run", {}) == ["agent", "run", "--dry-run"]
	assert mcp_args._build_args("agent_run", {"dry_run": False}) == ["agent", "run"]


def test_dry_run_default_tool_names_match_schema() -> None:
	# 动态守卫：TOOLS 里 schema 默认 dry_run=true 的工具必须全部登记，
	# 新增同类工具漏登记时这里失败（Issue #2414）。
	schema_defaults = {
		tool.name
		for tool in TOOLS
		if tool.input_schema.get("properties", {}).get("dry_run", {}).get("default") is True
	}
	assert schema_defaults == set(mcp_server._DRY_RUN_DEFAULT_TOOL_NAMES)
