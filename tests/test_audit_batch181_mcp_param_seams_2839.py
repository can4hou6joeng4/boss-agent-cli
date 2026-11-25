"""MCP 参数接缝三处修复（Issue #2839）。

1. ``boss_batch_greet`` 的 ``limit=0`` 只做一次搜索、不发出招呼，中断时不能
   报 ``outcome_unknown``。
2. 大小写不敏感的筛选枚举白名单必须包含 ``boss_batch_greet``。
3. ``boss_show.number`` 的 schema 必须声明 ``minimum: 1``，与 CLI 的 1-based
   校验一致。
"""

from __future__ import annotations

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_CASE_INSENSITIVE_ENUM_TOOLS,
	_argument_type_errors,
	_estimated_requests,
	_interrupted_failure,
	_read_interrupt_recovery,
	_tool_may_write,
)
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _details(envelope: dict[str, object]) -> dict[str, object]:
	error = envelope["error"]
	assert isinstance(error, dict)
	details = error["details"]
	assert isinstance(details, dict)
	return details


# ── 1. batch-greet limit=0 只读 ────────────────────────────────


def test_batch_greet_limit_zero_is_read_only() -> None:
	assert _tool_may_write("boss_batch_greet", {"query": "go", "limit": 0}) is False
	# 显式 dry_run=False 不影响 limit=0 的只读结论：候选在收集阶段就被截断。
	assert _tool_may_write("boss_batch_greet", {"query": "go", "limit": 0, "dry_run": False}) is False


def test_batch_greet_positive_or_default_limit_may_write() -> None:
	assert _tool_may_write("boss_batch_greet", {"query": "go", "limit": 1}) is True
	# 省略 limit 时映射层按 5 下发，仍可能真实发出招呼。
	assert _tool_may_write("boss_batch_greet", {"query": "go"}) is True
	assert _tool_may_write("boss_batch_greet", {"query": "go", "dry_run": True}) is False


def test_batch_greet_limit_zero_interrupted_is_replay_safe() -> None:
	may_write = _tool_may_write("boss_batch_greet", {"query": "go", "limit": 0})
	envelope = _interrupted_failure(("batch-greet", "go", "--count", "0"), "NETWORK_ERROR", b"", may_write=may_write)
	details = _details(envelope)

	assert details["outcome_unknown"] is False
	assert details["replay_safe"] is True
	error = envelope["error"]
	assert isinstance(error, dict)
	recovery = str(error["recovery_action"])
	assert "平台实际结果" not in recovery
	assert "active=online" not in recovery
	# 只读形态可直接重试，不必先核对本地 checkpoint。
	assert "重试" in recovery


def test_batch_greet_limit_zero_estimate_is_single_search() -> None:
	# 估算与只读口径同源：--count 0 只有一次搜索请求，没有写请求。
	assert _estimated_requests(("batch-greet", "go", "--count", "0")) == 1


def test_read_interrupt_recovery_for_batch_greet_read_forms() -> None:
	for argv in (
		("batch-greet", "go", "--count", "0"),
		("batch-greet", "go", "--dry-run"),
	):
		recovery = _read_interrupt_recovery(argv)
		assert "未发出任何招呼" in recovery
		assert "active=online" not in recovery


# ── 2. batch-greet 大小写不敏感枚举 ────────────────────────────


def test_batch_greet_stage_accepts_case_variants() -> None:
	assert _argument_type_errors("boss_batch_greet", {"query": "go", "stage": "a轮"}) == []
	assert _argument_type_errors("boss_batch_greet", {"query": "go", "job_type": "全职"}) == []
	# 映射层原样下发，由 CLI 的 click.Choice 归一为标准写法。
	assert _build_args("boss_batch_greet", {"query": "go", "stage": "a轮"}) == [
		"batch-greet",
		"go",
		"--stage",
		"a轮",
		"--count",
		"5",
	]


def test_batch_greet_still_rejects_unknown_enum_values() -> None:
	errors = _argument_type_errors("boss_batch_greet", {"query": "go", "stage": "不存在的阶段"})
	assert errors
	assert "不是允许的取值" in errors[0]


def test_case_insensitive_enum_tools_cover_all_filter_tools() -> None:
	# 目录里带这四种筛选枚举的工具都必须按大小写不敏感校验；新增工具若遗漏，
	# 入口会拒绝 CLI 本可接受的写法（Issue #2839）。
	filter_keys = {"industry", "scale", "stage", "job_type"}
	enum_tools = {
		tool.name
		for tool in build_tool_catalog()
		if any(
			isinstance(prop, dict) and prop.get("enum")
			for name, prop in tool.input_schema.get("properties", {}).items()
			if name in filter_keys
		)
	}
	assert enum_tools == set(_CASE_INSENSITIVE_ENUM_TOOLS)


# ── 3. boss_show 编号下界 ─────────────────────────────────────


def test_show_schema_declares_one_based_minimum() -> None:
	catalog = {tool.name: tool.input_schema for tool in build_tool_catalog()}
	number = catalog["boss_show"]["properties"]["number"]
	assert number["minimum"] == 1


def test_show_number_zero_and_negative_rejected_at_entry() -> None:
	for value in (0, -1):
		errors = _argument_type_errors("boss_show", {"number": value})
		assert errors, f"number={value} 应在入口被拒绝"
	assert _argument_type_errors("boss_show", {"number": 1}) == []
