"""向导 / MCP export 的 count 语义（Issue #2648）。

顶层 `boss export --count` 决定导出条数与页数预算；wizard 的 `export` goal
此前完全忽略 `count`，Agent 既不能限制数量，也不能保证拿到目标条数。
"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.services.pagination import export_page_budget
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_candidate_search,
)
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


class _FakePlatform:
	def __init__(self) -> None:
		self.calls: list[tuple[Any, ...]] = []

	def __enter__(self) -> "_FakePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


def _context(tmp_path) -> ActionContext:
	platform = _FakePlatform()
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args, **kwargs: platform,
	)


class _Result:
	def __init__(
		self, items: list[dict[str, Any]], *, has_more: bool, stop_reason: str | None = None
	) -> None:
		self.items = items
		self.has_more = has_more
		if stop_reason is not None:
			self.stop_reason = stop_reason


def _export(context: ActionContext, inputs: dict[str, Any], monkeypatch, result: _Result) -> tuple[dict, dict, dict]:
	captured: dict[str, Any] = {}

	def fake_search(platform_arg, cache, logger, search_inputs, *, limit=None, continue_when_has_more_unknown=False):
		captured["inputs"] = dict(search_inputs)
		captured["limit"] = limit
		captured["continue"] = continue_when_has_more_unknown
		return result

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", fake_search)
	step = DEFAULT_ACTIONS["candidate_export"](context, inputs, {})
	return step.data, captured["inputs"], {"limit": captured["limit"], "continue": captured["continue"]}


def test_export_count_passes_limit_and_expands_page_budget(tmp_path, monkeypatch):
	"""count=50 要按顶层导出的页数口径扫描，并把 limit 交给搜索管线。"""
	items = [{"job_id": f"job-{index}"} for index in range(50)]
	data, search_inputs, extra = _export(
		_context(tmp_path),
		{"query": "Golang", "count": 50, "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result(items, has_more=True, stop_reason="limit_reached"),
	)

	assert extra["limit"] == 50
	assert extra["continue"] is True
	assert search_inputs["max_pages"] == export_page_budget(50) == 9
	assert data["count"] == 50
	assert data["requested"] == 50
	assert data["stop_reason"] == "count_reached"
	assert data["truncated"] is False


def test_export_count_respects_explicit_max_pages(tmp_path, monkeypatch):
	"""显式 max_pages 是调用方的预算，不能被 count 的估页覆盖。"""
	data, search_inputs, extra = _export(
		_context(tmp_path),
		{"query": "Golang", "count": 10, "max_pages": 2, "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result([{"job_id": "job-1"}], has_more=True, stop_reason="page_budget_exhausted"),
	)

	assert extra["limit"] == 10
	assert search_inputs["max_pages"] == 2
	assert data["count"] == 1
	assert data["requested"] == 10
	# 页数预算耗尽：结果只是前缀，与顶层 export 的 stop_reason 同词汇。
	assert data["stop_reason"] == "page_budget_exhausted"
	assert data["truncated"] is True


def test_export_count_shortfall_without_more_is_not_truncated(tmp_path, monkeypatch):
	"""平台已明确没有下一页时，少于 count 是完整结果，不能报截断。"""
	data, _, _ = _export(
		_context(tmp_path),
		{"query": "Golang", "count": 50, "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result([{"job_id": "job-1"}], has_more=False, stop_reason="has_more_false"),
	)

	assert data["count"] == 1
	assert data["stop_reason"] == "platform_has_more_false"
	assert data["truncated"] is False


def test_export_active_early_stop_marks_truncated(tmp_path, monkeypatch):
	"""活跃度筛选提前停止且没取满 count 时，wizard 与顶层 export 同口径报截断（Issue #2712）。"""
	data, _, _ = _export(
		_context(tmp_path),
		{"query": "Golang", "count": 50, "active": "online", "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result([{"job_id": "job-1"}], has_more=True, stop_reason="active_target_reached"),
	)

	assert data["count"] == 1
	assert data["requested"] == 50
	assert data["stop_reason"] == "active_target_reached"
	assert data["truncated"] is True


def test_export_active_stop_with_full_count_is_not_truncated(tmp_path, monkeypatch):
	"""恰好取满 count 时即使终止原因是 active_target_reached 也是完整结果。"""
	items = [{"job_id": f"job-{index}"} for index in range(10)]
	data, _, _ = _export(
		_context(tmp_path),
		{"query": "Golang", "count": 10, "active": "online", "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result(items, has_more=True, stop_reason="active_target_reached"),
	)

	assert data["count"] == 10
	assert data["requested"] == 10
	assert data["stop_reason"] == "active_target_reached"
	assert data["truncated"] is False


def test_export_without_count_keeps_legacy_behavior(tmp_path, monkeypatch):
	"""不传 count 时不改默认页数预算，也不新增信封字段（保持旧契约）。"""
	data, search_inputs, extra = _export(
		_context(tmp_path),
		{"query": "Golang", "output": str(tmp_path / "out.json")},
		monkeypatch,
		_Result([{"job_id": "job-1"}], has_more=True),
	)

	assert extra["limit"] is None
	assert extra["continue"] is False
	assert "max_pages" not in search_inputs
	assert set(data) == {"path", "format", "count"}


@pytest.mark.parametrize("count", [0, -1, "abc", True, 2.5])
def test_export_rejects_invalid_count_before_any_request(tmp_path, monkeypatch, count):
	"""非法 count 必须在平台请求与写文件之前报 INVALID_PARAM。"""
	calls: list[str] = []

	def must_not_run(*args, **kwargs):
		calls.append("search")
		raise AssertionError("非法 count 不应发起搜索")

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", must_not_run)
	context = _context(tmp_path)

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_export"](
			context, {"query": "Golang", "count": count, "output": str(tmp_path / "out.json")}, {}
		)

	assert calls == []
	assert not (tmp_path / "out.json").exists()


def test_export_count_above_trusted_limit_is_rejected(tmp_path):
	"""与 CLI 共用可信上限：超长整数不能进入平台参数。"""
	from boss_agent_cli.services.pagination import MAX_TRUSTED_COUNT

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path),
			{"query": "Golang", "count": MAX_TRUSTED_COUNT + 1, "output": str(tmp_path / "out.json")},
			{},
		)


def test_execute_candidate_search_forwards_limit_to_pipeline():
	"""共享执行器把 limit 原样交给 run_search_pipeline，由其在取满后停止。"""
	captured: dict[str, Any] = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return _Result([], has_more=False)

	execute_candidate_search(object(), object(), Logger(), {"query": "Golang"}, pipeline=pipeline, limit=7)

	assert captured["limit"] == 7


def test_export_page_budget_matches_top_level_cli_heuristic():
	assert export_page_budget(1) == 6
	assert export_page_budget(15) == 6
	assert export_page_budget(16) == 7
	assert export_page_budget(50) == 9


def test_export_invalid_format_still_rejected_with_count(tmp_path):
	"""count 合法时 format 仍在任何平台请求之前被拒绝（回归护栏）。"""
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path), {"query": "Golang", "count": 5, "format": "xml", "output": str(tmp_path / "out")}, {}
		)

	assert exc_info.value.code == "INVALID_PARAM"
