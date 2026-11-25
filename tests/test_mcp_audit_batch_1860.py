"""MCP 边界缺陷批次（Issue #1860–#1865）。"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from boss_agent_cli import mcp_args, mcp_server, mcp_tools


# ── Issue #1860：筛选类只读命令的超时估算与恢复文案 ──────────────────


@pytest.mark.parametrize(
	"argv",
	[
		("search", "golang", "--preset", "p1"),
		("search", "--preset=p1"),
		("watch", "run", "w1"),
	],
)
def test_filter_tools_estimate_detail_pages_worst_case(argv: tuple[str, ...]) -> None:
	"""preset 可能带福利 / 活跃度筛选，watch run 也一样：都要按 5 页 × 16 次请求估算。"""
	assert mcp_server._estimated_requests(argv) == 80
	assert mcp_server._cli_timeout(argv) == 900.0


def test_plain_search_and_other_watch_subcommands_keep_default() -> None:
	assert mcp_server._estimated_requests(("search", "golang")) == 1
	assert mcp_server._cli_timeout(("search", "golang")) == 120.0
	assert mcp_server._estimated_requests(("watch", "list")) is None
	assert mcp_server._cli_timeout(("watch", "list")) == 120.0


def test_filter_read_timeout_recovery_does_not_name_missing_options() -> None:
	"""search / watch 没有 limit / count，恢复文案不能提示不存在的参数。"""
	for argv in (("search", "golang", "--preset", "p1"), ("watch", "run", "w1")):
		result = mcp_server._interrupted_failure(argv, "WORKFLOW_TIMEOUT", b"", may_write=False)
		recovery = result["error"]["recovery_action"]
		assert "limit/count" not in recovery
		assert "active=online" in recovery


@pytest.mark.parametrize("code", ["WORKFLOW_TIMEOUT", "WORKFLOW_STOPPED"])
@pytest.mark.parametrize("argv", [("status",), ("crawl", "status", "demo-run")])
def test_generic_read_interrupt_recovery_uses_existing_arguments(argv: tuple[str, ...], code: str) -> None:
	result = mcp_server._interrupted_failure(argv, code, b"", may_write=False)
	error = result["error"]
	assert error["code"] == code
	assert error["details"]["replay_safe"] is True
	assert error["details"]["outcome_unknown"] is False
	recovery = error["recovery_action"]
	assert "按原参数重新调用" in recovery
	assert all(option not in recovery for option in ("limit", "count", "active="))


@patch("boss_agent_cli.mcp_server.subprocess.run")
def test_sync_run_boss_filter_timeout_uses_filter_recovery(mock_run) -> None:
	mock_run.side_effect = subprocess.TimeoutExpired("boss", 900)

	result = mcp_server._run_boss("search", "golang", "--preset", "p1", may_write=False)

	assert result["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert "limit/count" not in result["error"]["recovery_action"]
	assert "active=online" in result["error"]["recovery_action"]


# ── Issue #1861：hr_jobs / hr_jobs_detail 的合规命令映射 ─────────────


def test_hr_jobs_compliance_commands_use_recruiter_identifiers() -> None:
	assert mcp_tools._compliance_command_for_tool("boss_hr_jobs") == "recruiter-jobs-list"
	assert mcp_tools._compliance_command_for_tool("boss_hr_jobs_detail") == "recruiter-jobs-detail"


@pytest.mark.parametrize(
	"action, expected",
	[
		("list", "recruiter-jobs-list"),
		("online", "recruiter-jobs-online"),
		("offline", "recruiter-jobs-offline"),
	],
)
def test_hr_jobs_compliance_command_follows_action(action: str, expected: str) -> None:
	assert mcp_tools._compliance_command_for_tool("boss_hr_jobs", {"action": action}) == expected


def test_hr_jobs_compliance_unknown_action_falls_back_to_static_override() -> None:
	"""未识别的 action 交给 CLI 拒绝，映射层不猜命令标识。"""
	assert mcp_tools._compliance_command_for_tool("boss_hr_jobs", {"action": "delete"}) == "recruiter-jobs-list"
	assert mcp_tools._compliance_command_for_tool("boss_hr_jobs", {"action": 3}) == "recruiter-jobs-list"


# ── Issue #1862：boss_ai_chat_coach 的显式空 style ───────────────────


def test_chat_coach_forwards_explicit_empty_style() -> None:
	args = mcp_args._build_args("boss_ai_chat_coach", {"chat_text": "hi", "style": ""})

	assert args == ["ai", "chat-coach", "hi", "--style", ""]


def test_chat_coach_omits_style_when_absent() -> None:
	assert mcp_args._build_args("boss_ai_chat_coach", {"chat_text": "hi"}) == ["ai", "chat-coach", "hi"]


# ── Issue #1863：同步入口的本地参数拒绝不能套用写中断文案 ────────────


@pytest.mark.parametrize("may_write", [True, False])
def test_sync_run_boss_unsafe_argv_reports_no_platform_effect(may_write: bool) -> None:
	result = mcp_server._run_boss("status", "a\x00b", may_write=may_write)
	error = result["error"]

	assert result["ok"] is False
	assert error["code"] == "INVALID_PARAM"
	assert error["details"]["outcome_unknown"] is False
	assert "checkpoint" not in error["recovery_action"]
	assert "子进程未启动" in error["recovery_action"]


# ── Issue #1864：_default_boss_bin 只把「不存在」当未找到 ────────────


def test_default_boss_bin_returns_sibling_regular_file(tmp_path: Path) -> None:
	boss = tmp_path / "boss"
	boss.write_text("#!/bin/sh\n", encoding="utf-8")

	assert mcp_server._default_boss_bin(executable=str(tmp_path / "python3"), windows=False) == str(boss)


def test_default_boss_bin_skips_directory(tmp_path: Path) -> None:
	(tmp_path / "boss").mkdir()

	result = mcp_server._default_boss_bin(executable=str(tmp_path / "python3"), windows=False)
	assert result == mcp_server.DEFAULT_BOSS_BIN


def test_default_boss_bin_surfaces_non_missing_errors(tmp_path: Path) -> None:
	"""权限 / 链接循环等错误要显式暴露，不能静默回退 PATH 上的另一份 boss。"""
	loop = tmp_path / "boss"
	loop.symlink_to(loop)

	with pytest.raises(OSError):
		mcp_server._default_boss_bin(executable=str(tmp_path / "python3"), windows=False)


# ── Issue #1865：hr 工具的空 job_id 不能被吞成未传 ───────────────────


@pytest.mark.parametrize(
	"tool_name, argv",
	[
		("boss_hr_applications", ["hr", "applications", "--job-id", ""]),
		("boss_hr_chat", ["hr", "chat", "--job-id", ""]),
		("boss_hr_last_messages", ["hr", "last-messages", "--job-id", ""]),
	],
)
def test_hr_job_id_uses_existence_check(tool_name: str, argv: list[str]) -> None:
	assert mcp_args._build_args(tool_name, {"job_id": ""}) == argv


def test_hr_job_id_absent_keeps_option_out() -> None:
	assert mcp_args._build_args("boss_hr_applications", {}) == ["hr", "applications"]
	assert mcp_args._build_args("boss_hr_chat", {}) == ["hr", "chat"]
	assert mcp_args._build_args("boss_hr_last_messages", {}) == ["hr", "last-messages"]
