"""向导 search 的 with_score 输入与命令层共用评分实现（Issue #2665）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_candidate_search,
)
from boss_agent_cli.wizard.models import WorkflowInputError


class _Platform:
	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


def _context(tmp_path, factory) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def test_execute_search_without_score_keeps_pipeline_items():
	items = [{"job_id": "job-1", "title": "Golang 工程师", "match_score": 10}]

	def pipeline(*args, **kwargs):
		return SimpleNamespace(items=items, has_more=False, has_more_reported=False, total=1)

	result = execute_candidate_search(object(), object(), Logger(), {"query": "Golang"}, pipeline=pipeline)

	assert result.items == items
	assert "match_reasons" not in result.items[0]


def test_execute_search_with_score_attaches_reasons_and_recomputes_score():
	items = [
		{
			"job_id": "job-1",
			"title": "Golang 工程师",
			"salary": "30-50K",
			"city": "上海",
			"welfare": ["五险一金"],
			"match_score": 10,
		}
	]

	def pipeline(*args, **kwargs):
		return SimpleNamespace(items=[dict(item) for item in items], has_more=False, has_more_reported=False, total=1)

	result = execute_candidate_search(
		object(), object(), Logger(), {"query": "Golang", "city": "上海"}, pipeline=pipeline, with_score=True
	)

	item = result.items[0]
	assert "关键词直接命中" in item["match_reasons"]
	assert "城市匹配" in item["match_reasons"]
	# 城市 25 + 关键词 10 + 福利完整 5：与命令层 score_job_dict 同一口径。
	assert item["match_score"] == 40
	assert isinstance(item["mismatch_reasons"], list)


def _run_action(tmp_path, monkeypatch, inputs: dict[str, Any], items: list[dict[str, Any]]):
	captured: dict[str, Any] = {}

	class _Result:
		def __init__(self) -> None:
			self.items = items
			self.has_more_reported = False
			self.total = len(items)

	def fake_search(platform, cache, logger, search_inputs, **kwargs):
		captured.update(kwargs)
		return _Result()

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", fake_search)
	context = _context(tmp_path, lambda *args, **kwargs: _Platform())
	data = dict(DEFAULT_ACTIONS["candidate_search"](context, inputs, {}).data)
	return data, captured


def test_search_action_default_does_not_pass_with_score(tmp_path, monkeypatch):
	data, captured = _run_action(tmp_path, monkeypatch, {"query": "Go"}, [{"match_score": 1, "match_reasons": []}])

	assert "with_score" not in captured
	assert data["items"][0]["match_reasons"] == []


def test_search_action_with_score_passes_flag_and_sorts_after_scoring(tmp_path, monkeypatch):
	items = [
		{"job_id": "low", "match_score": 10, "match_reasons": ["福利信息完整"]},
		{"job_id": "high", "match_score": 90, "match_reasons": ["城市匹配"]},
	]

	data, captured = _run_action(
		tmp_path, monkeypatch, {"query": "Go", "with_score": True, "sort": "score"}, items
	)

	assert captured["with_score"] is True
	assert [item["job_id"] for item in data["items"]] == ["high", "low"]


@pytest.mark.parametrize("value", ["true", 1, [], {"enabled": True}])
def test_search_action_rejects_non_bool_with_score_before_platform(tmp_path, value):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非布尔 with_score 不应打开平台或发起搜索")

	context = _context(tmp_path, must_not_open)
	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_search"](context, {"query": "Go", "with_score": value}, {})
