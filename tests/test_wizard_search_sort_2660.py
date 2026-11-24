"""向导 search 的 sort 输入与 CLI 共用排序实现（Issue #2660）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.services.search_order import sort_search_items
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


class _Platform:
	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


class _Result:
	def __init__(self, items: list[dict[str, Any]]) -> None:
		self.items = items
		self.has_more_reported = False
		self.total = len(items)


def _context(tmp_path, factory) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def _run(tmp_path, monkeypatch, inputs: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
	context = _context(tmp_path, lambda *args, **kwargs: _Platform())
	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions.execute_candidate_search",
		lambda *args, **kwargs: _Result(items),
	)
	return dict(DEFAULT_ACTIONS["candidate_search"](context, inputs, {}).data)


def test_search_defaults_to_relevance_order(tmp_path, monkeypatch):
	items = [{"job_id": "first", "match_score": 10}, {"job_id": "second", "match_score": 90}]

	data = _run(tmp_path, monkeypatch, {"query": "Go"}, items)
	assert [item["job_id"] for item in data["items"]] == ["first", "second"]

	explicit = _run(tmp_path, monkeypatch, {"query": "Go", "sort": "relevance"}, items)
	assert [item["job_id"] for item in explicit["items"]] == ["first", "second"]


def test_search_score_sorts_and_tolerates_type_drift(tmp_path, monkeypatch):
	items = [
		{"job_id": "str-score", "match_score": "90"},
		{"job_id": "low", "match_score": 10},
		{"job_id": "bool-score", "match_score": True},
		{"job_id": "high", "match_score": 50},
		{"job_id": "missing"},
	]

	data = _run(tmp_path, monkeypatch, {"query": "Go", "sort": "score"}, items)

	# 字符串 / 布尔 / 缺失的分数按 0 处理；同分保持原相对顺序（稳定排序）。
	assert [item["job_id"] for item in data["items"]] == [
		"high", "low", "str-score", "bool-score", "missing",
	]


@pytest.mark.parametrize("sort", ["", " ", "newest", "SCORE"])
def test_search_rejects_invalid_sort_before_platform_request(tmp_path, monkeypatch, sort):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非法 sort 不应打开平台或发起搜索")

	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions.execute_candidate_search",
		lambda *args, **kwargs: pytest.fail("非法 sort 不应发起搜索"),
	)
	context = _context(tmp_path, must_not_open)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_search"](context, {"query": "Go", "sort": sort}, {})

	assert exc_info.value.code == "INVALID_PARAM"


def test_search_rejects_non_string_sort(tmp_path, monkeypatch):
	monkeypatch.setattr(
		"boss_agent_cli.wizard.actions.execute_candidate_search",
		lambda *args, **kwargs: pytest.fail("非字符串 sort 不应发起搜索"),
	)
	context = _context(tmp_path, lambda *args, **kwargs: pytest.fail("非字符串 sort 不应打开平台"))

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_search"](context, {"query": "Go", "sort": 1}, {})


def test_shared_sort_helper_matches_command_alias():
	from boss_agent_cli.commands.search import _sort_search_items

	items = [{"match_score": 1}, {"match_score": 9}, {"match_score": "7"}]
	assert sort_search_items(items, "score") == _sort_search_items(items, "score")
	assert sort_search_items(items, "relevance") is items
