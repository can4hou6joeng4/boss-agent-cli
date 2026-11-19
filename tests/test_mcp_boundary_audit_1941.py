"""MCP 参数映射与子进程边界（Issue #1941–#1943）。"""

from __future__ import annotations

import asyncio
import errno

import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.mcp_args import _build_args

_HUGE = '{"ok": true, "data": ' + "1" * 5000 + "}"


# ── Issue #1941：显式空串必须到达 CLI ───────────────────────────────


@pytest.mark.parametrize(("key", "flag"), [("url", "--url"), ("preset", "--preset")])
def test_search_forwards_explicit_empty_value(key: str, flag: str) -> None:
	args = _build_args("boss_search", {"query": "python", key: ""})

	assert args[args.index(flag) + 1] == ""


def test_search_none_still_means_unset() -> None:
	assert _build_args("boss_search", {"query": "python", "url": None, "preset": None}) == [
		"search",
		"python",
	]


@pytest.mark.parametrize("tool", ["boss_ai_cover_letter", "boss_ai_resume_optimize"])
def test_ai_tools_forward_empty_jd_for_cli_validation(tool: str) -> None:
	args = _build_args(tool, {"resume": "r", "jd_text": "", "job_id": "J"})

	assert args[args.index("--jd") + 1] == ""
	assert args[args.index("--job-id") + 1] == "J"


# ── Issue #1942：超长整数不得击穿解析 ───────────────────────────────


@pytest.mark.parametrize("may_write", [True, False])
def test_parse_cli_output_reports_huge_integer_as_cli_error(may_write: bool) -> None:
	result = mcp_server._parse_cli_output(("search", "q"), _HUGE, "", may_write=may_write)

	assert result["ok"] is False
	assert result["error"]["code"] == "CLI_ERROR"


def test_interrupted_failure_survives_huge_integer_stdout() -> None:
	result = mcp_server._interrupted_failure(
		("search", "q"), "WORKFLOW_TIMEOUT", _HUGE.encode(), may_write=False
	)

	assert result["ok"] is False
	assert result["error"]["code"] == "WORKFLOW_TIMEOUT"


# ── Issue #1943：参数过长不是安装问题 ───────────────────────────────


def test_argv_too_long_reports_invalid_param() -> None:
	result = mcp_server._spawn_failure(
		("search", "q"), OSError(errno.E2BIG, "Argument list too long")
	)

	assert result["ok"] is False
	assert result["error"]["code"] == "INVALID_PARAM"
	assert "缩短" in result["error"]["recovery_action"]
	assert "PATH" not in result["error"]["recovery_action"]


def test_missing_binary_still_reports_install_guidance() -> None:
	result = mcp_server._spawn_failure(
		("search", "q"), FileNotFoundError(errno.ENOENT, "No such file")
	)

	assert result["error"]["code"] == "CLI_ERROR"
	assert "PATH" in result["error"]["recovery_action"]


def test_async_runner_maps_e2big(monkeypatch: pytest.MonkeyPatch) -> None:
	async def too_long(*args: object, **kwargs: object) -> None:
		raise OSError(errno.E2BIG, "Argument list too long")

	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", too_long)

	result = asyncio.run(mcp_server._run_boss_async("search", "q", may_write=False))

	assert result["error"]["code"] == "INVALID_PARAM"
