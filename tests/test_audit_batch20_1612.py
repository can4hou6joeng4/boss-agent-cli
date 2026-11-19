"""批次二十回归测试：Issue #1612、#1614–#1617。

全部测试离线运行；不调用平台 API，不启动浏览器。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest

from boss_agent_cli.commands.mark import _LABEL_MAP, _resolve_label
from boss_agent_cli.crawler.exporter import CrawlExportError, write_run_outputs
from boss_agent_cli.mcp_server import (
	_argument_type_errors,
	_cli_timeout,
	_estimated_requests,
	_run_boss,
)
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
_HUGE_INT = "1" + "0" * 500
_CHECK_COMMAND = "boss hr chatmsg 42"


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(role="recruiter", platform="zhipin", goal="offline", inputs={}, requested_steps=steps, mode="headless")


def _context(data_dir: Path) -> ActionContext:
	return ActionContext(data_dir=data_dir, platform="zhipin", role="recruiter", logger=MagicMock())


# ── #1612：同步 MCP 入口与 HR 估算的整数边界 ───────────────────────


@pytest.mark.parametrize(
	"args",
	[
		("hr", "chat", "--summary-limit", _HUGE_INT),
		("hr", "last-messages", "--limit", _HUGE_INT),
	],
)
def test_huge_hr_numbers_return_standard_error_envelope(args: tuple[str, ...]) -> None:
	result = _run_boss(*args)

	assert set(result) == _ENVELOPE_KEYS
	assert result["ok"] is False
	assert result["error"]["code"] == "INVALID_PARAM"
	assert result["error"]["recoverable"] is False


@pytest.mark.parametrize(
	"args",
	[
		("hr", "chat", "--summary-limit", _HUGE_INT),
		("hr", "last-messages", "--limit", _HUGE_INT),
	],
)
def test_huge_hr_numbers_have_bounded_timeout(args: tuple[str, ...]) -> None:
	assert _estimated_requests(args) <= 15
	assert _cli_timeout(args) == 900.0


def test_normal_hr_timeout_unchanged() -> None:
	# 无 --friend-id 时先取一页沟通列表再拉消息：1 次列表 + 1 批（Issue #2025）。
	assert _estimated_requests(("hr", "last-messages", "--limit", "50")) == 2
	assert _cli_timeout(("hr", "chat", "--summary-limit", "50")) == 160.0
	assert _cli_timeout(("hr", "last-messages", "--limit", "50")) == 160.0


# ── #1614：数组元素的数值边界 ──────────────────────────────────────


def test_array_item_minimum_rejected_before_spawn() -> None:
	errors = _argument_type_errors("boss_hr_last_messages", {"friend_ids": [0, 2]})

	assert errors == ["friend_ids[0]=0 不能小于 1"]


def test_valid_array_items_remain_accepted() -> None:
	assert _argument_type_errors("boss_hr_last_messages", {"friend_ids": [1, 2]}) == []


# ── #1615：标签输入与 schema 三处真源一致 ──────────────────────────


def test_mark_enum_covers_names_and_numeric_ids() -> None:
	expected = set(_LABEL_MAP) | {str(value) for value in _LABEL_MAP.values()}
	native = SCHEMA_DATA["commands"]["mark"]["options"]["--label"]["enum"]
	mcp = next(tool for tool in build_tool_catalog() if tool.name == "boss_mark").input_schema["properties"]["tag"]["enum"]

	assert set(native) == expected
	assert set(mcp) == expected
	for identifier in ("1", "2", "3", "4", "5", "6", "7", "8", "11"):
		assert _resolve_label(identifier) == int(identifier)


@pytest.mark.parametrize("value", ["招呼", "面", "已交换"])
def test_mark_rejects_fuzzy_name_substrings(value: str) -> None:
	with pytest.raises(click.BadParameter, match="未知标签"):
		_resolve_label(value)


# ── #1617：缺 replay_safe 的旧格式在途写不得重放 ───────────────────


def test_legacy_action_unconfirmed_resume_does_not_replay(tmp_path: Path) -> None:
	step = "recruiter_reply"
	legacy_error = {
		"code": "ACTION_UNCONFIRMED",
		"message": "写请求在途，结果尚未确认",
		"recoverable": False,
		"recovery_action": _CHECK_COMMAND,
	}
	calls: list[str] = []

	with WorkflowStore(tmp_path) as store:
		store.create("run-legacy-unconfirmed", _plan(step))
		store.update_run("run-legacy-unconfirmed", status="running", current_step=step)
		store.update_step("run-legacy-unconfirmed", step, status="running", error=legacy_error)

		def replay(context: object, inputs: object, prior: object) -> StepResult:
			calls.append("replay")
			return StepResult({"sent": True})

		run = WorkflowRunner(store, {step: replay}).run(
			_plan(step), _context(tmp_path), run_id="run-legacy-unconfirmed"
		)

	assert calls == []
	assert run["status"] == "failed"
	assert run["error"]["code"] == "ACTION_UNCONFIRMED"
	assert run["error"]["replay_safe"] is False
	assert _CHECK_COMMAND in run["error"]["recovery_action"]


# ── #1616：三份采集产物的替换失败回滚 ──────────────────────────────


def test_export_replace_failure_keeps_all_old_artifacts(tmp_path: Path) -> None:
	(tmp_path / "jobs.json").write_text("OLD_JSON", encoding="utf-8")
	(tmp_path / "jobs.csv").write_text("OLD_CSV", encoding="utf-8")
	(tmp_path / "jobs.xlsx").mkdir()

	with pytest.raises(CrawlExportError):
		write_run_outputs(tmp_path, [{"title": "NEW"}])

	assert (tmp_path / "jobs.json").read_text(encoding="utf-8") == "OLD_JSON"
	assert (tmp_path / "jobs.csv").read_text(encoding="utf-8") == "OLD_CSV"
	assert not [path for path in tmp_path.iterdir() if path.name.startswith(".jobs.")]


def test_export_success_replaces_all_artifacts(tmp_path: Path) -> None:
	result = write_run_outputs(tmp_path, [{"title": "NEW"}])

	assert set(result) == {"json", "csv", "xlsx"}
	assert "NEW" in (tmp_path / "jobs.json").read_text(encoding="utf-8")
	assert (tmp_path / "jobs.csv").exists()
	assert (tmp_path / "jobs.xlsx").exists()
	assert not [path for path in tmp_path.iterdir() if path.name.startswith(".jobs.")]


# ── #1755：采集产物与目录的符号链接 / 特殊文件保护 ────────────────


@pytest.mark.parametrize("name", ["jobs.json", "jobs.csv", "jobs.xlsx"])
def test_export_refuses_symlink_artifact_without_touching_link_or_target(tmp_path: Path, name: str) -> None:
	target = tmp_path / "outside-artifact"
	target.write_text("OLD", encoding="utf-8")
	link = tmp_path / name
	try:
		link.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(CrawlExportError) as exc_info:
		write_run_outputs(tmp_path, [{"title": "NEW"}])

	assert "符号链接" in str(exc_info.value)
	assert link.is_symlink()
	assert target.read_text(encoding="utf-8") == "OLD"
	assert not [path for path in tmp_path.iterdir() if path.name.startswith(".jobs.")]


def test_export_refuses_symlinked_output_dir(tmp_path: Path) -> None:
	real_dir = tmp_path / "real-output"
	real_dir.mkdir()
	link = tmp_path / "crawl-output"
	try:
		link.symlink_to(real_dir, target_is_directory=True)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(CrawlExportError) as exc_info:
		write_run_outputs(link, [{"title": "NEW"}])

	assert "目录" in str(exc_info.value)
	assert list(real_dir.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_export_refuses_fifo_artifact_without_replacing_it(tmp_path: Path) -> None:
	import stat

	artifact = tmp_path / "jobs.json"
	os.mkfifo(artifact)

	with pytest.raises(CrawlExportError) as exc_info:
		write_run_outputs(tmp_path, [{"title": "NEW"}])

	assert "不是普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO(artifact.lstat().st_mode)
