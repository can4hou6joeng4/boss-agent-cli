"""向导 search 的活跃度筛选统计与 CLI `hints.active_filter` 同口径（Issue #2720）。

`boss search --active` 会告诉调用方多少职位因活跃度未知被排除、查了多少详情；
向导 `search` goal 此前只返回 items / pagination，Agent 只能看到条数偏少。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


class _Platform:
	def __init__(self) -> None:
		self.constructed = True

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


class _Result:
	def __init__(self) -> None:
		self.items = [{"job_id": "job-1"}]
		self.has_more = False
		self.has_more_reported = False
		self.total = 1
		self.stats = SimpleNamespace(active_rejected=3, active_unknown=2, detail_requests=4)
		self.active_unknown_descs = {"本周活跃": 2}


def _context(tmp_path, *, allow_platform: bool = True) -> ActionContext:
	def factory(*args: object, **kwargs: object) -> _Platform:
		if not allow_platform:
			raise AssertionError("非法输入不应构造平台实例")
		return _Platform()

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def _install_fake_search(monkeypatch) -> dict[str, Any]:
	captured: dict[str, Any] = {}

	def fake_search(platform_arg, cache, logger, search_inputs, **kwargs):
		captured["inputs"] = dict(search_inputs)
		return _Result()

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", fake_search)
	return captured


def test_search_writes_active_filter_evidence(tmp_path, monkeypatch):
	_install_fake_search(monkeypatch)
	step = DEFAULT_ACTIONS["candidate_search"](
		_context(tmp_path), {"query": "Python", "active": "3d"}, {}
	)

	hint = step.data["active_filter"]
	assert hint["level"] == "3d"
	assert hint["rejected"] == 3
	assert hint["unknown_excluded"] == 2
	assert hint["detail_lookups"] == 4
	assert hint["unknown_descs"] == {"本周活跃": 2}


def test_search_without_active_keeps_legacy_data_shape(tmp_path, monkeypatch):
	_install_fake_search(monkeypatch)
	step = DEFAULT_ACTIONS["candidate_search"](_context(tmp_path), {"query": "Python"}, {})

	assert set(step.data) == {"items", "pagination"}


def test_invalid_active_rejected_before_platform(tmp_path, monkeypatch):
	_install_fake_search(monkeypatch)
	with pytest.raises(WorkflowActionError, match="未知活跃度"):
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, allow_platform=False),
			{"query": "Python", "active": "3years"},
			{},
		)
