"""wizard 自由输入与本地配置边界必须在产生平台请求 / 本地写入之前失败（Issue #1330）。"""

from typing import Any, cast

import pytest

import boss_agent_cli.wizard.actions as actions
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_candidate_search,
)
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path, *, config: Any = None):
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		config=config,
	)


def _search_must_not_run(*args, **kwargs):
	raise AssertionError("平台搜索不应在参数校验失败时被调用")


@pytest.mark.parametrize("raw_params", ["oops", ["a"], 123])
def test_candidate_search_rejects_non_mapping_raw_params(raw_params):
	with pytest.raises(WorkflowInputError) as exc_info:
		execute_candidate_search(
			None,
			None,
			None,
			{"query": "go", "raw_params": raw_params},
			pipeline=_search_must_not_run,
		)
	assert "raw_params" in str(exc_info.value)


def test_candidate_search_accepts_missing_or_null_raw_params():
	sentinel = object()
	assert (
		execute_candidate_search(
			None, None, None, {"query": "go", "raw_params": None}, pipeline=lambda *a, **k: sentinel
		)
		is sentinel
	)
	assert (
		execute_candidate_search(None, None, None, {"query": "go"}, pipeline=lambda *a, **k: sentinel)
		is sentinel
	)


def test_export_rejects_bad_format_before_search(tmp_path, monkeypatch):
	monkeypatch.setattr(actions, "execute_candidate_search", _search_must_not_run)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](_context(tmp_path), {"query": "go", "format": "xml"}, {})
	assert exc_info.value.code == "INVALID_PARAM"


def test_export_rejects_directory_output_before_search(tmp_path, monkeypatch):
	monkeypatch.setattr(actions, "execute_candidate_search", _search_must_not_run)
	output_dir = tmp_path / "out"
	output_dir.mkdir()
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path), {"query": "go", "output": str(output_dir)}, {}
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "目录" in str(exc_info.value)


def test_export_rejects_non_string_output_before_search(tmp_path, monkeypatch):
	monkeypatch.setattr(actions, "execute_candidate_search", _search_must_not_run)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](_context(tmp_path), {"query": "go", "output": ["x"]}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "output" in str(exc_info.value)


def test_export_rejects_blocked_parent_before_search(tmp_path, monkeypatch):
	monkeypatch.setattr(actions, "execute_candidate_search", _search_must_not_run)
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path), {"query": "go", "output": str(blocker / "result.json")}, {}
		)
	assert exc_info.value.code == "LOCAL_IO_ERROR"


def test_crawl_start_rejects_non_mapping_crawl_config(tmp_path):
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["crawl_start"](
			_context(tmp_path, config={"crawl": "oops"}), {"city": "北京"}, {}
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "config reset crawl" in exc_info.value.recovery_action
	assert str(tmp_path.resolve()) in exc_info.value.recovery_action


def test_crawl_start_rejects_non_mapping_config(tmp_path):
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["crawl_start"](
			_context(tmp_path, config=cast(Any, ["oops"])), {"city": "北京"}, {}
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "config.json" in exc_info.value.recovery_action
