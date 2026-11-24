"""向导 / MCP 搜索文本的 UTF-8 可编码性（Issue #2651）。

CLI 的 search / export / watch add 在入口拒绝含孤立代理项的搜索文本
（#2528、#2543）；向导此前只在平台请求或写库时才暴露，watch add 甚至会
存下永远跑不通的记录。这里钉住两条入口的 INVALID_PARAM 口径。
"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_candidate_search,
)
from boss_agent_cli.wizard.runner import WorkflowActionError

SURROGATE = "go\udcff"


class _FakePlatform:
	def __init__(self) -> None:
		self.calls: list[tuple[Any, ...]] = []

	def __enter__(self) -> "_FakePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def search_jobs(self, query: str, **kwargs: Any) -> dict[str, Any]:
		self.calls.append(("search_jobs", query))
		return {"code": 0, "data": {"jobList": [], "hasMore": False}}


def _context(tmp_path) -> tuple[ActionContext, _FakePlatform]:
	platform = _FakePlatform()
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args, **kwargs: platform,
	)
	return context, platform


def _saved(tmp_path) -> list[dict[str, object]]:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		return cache.list_saved_searches()


@pytest.mark.parametrize(
	"inputs",
	[
		{"action": "add", "name": "daily", "query": SURROGATE},
		{"action": "add", "name": "daily", "query": "Python", "welfare": ["双休\udcff"]},
	],
)
def test_watch_add_rejects_unencodable_text_before_write(tmp_path, inputs):
	"""不可编码的关键词 / 福利不能写进 watch 记录（否则每次 run 都 SAVED_SEARCH_INVALID）。"""
	context, _ = _context(tmp_path)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](context, inputs, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert exc_info.value.recoverable is True
	assert _saved(tmp_path) == []


def test_watch_add_still_rejects_invalid_city(tmp_path):
	"""换成存量校验后，城市等筛选错误仍是 INVALID_PARAM（回归护栏）。"""
	context, _ = _context(tmp_path)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](
			context, {"action": "add", "name": "daily", "query": "后端", "city": "北京市"}, {}
		)

	assert exc_info.value.code == "INVALID_PARAM"
	assert _saved(tmp_path) == []


@pytest.mark.parametrize("action", ["candidate_search", "candidate_export"])
def test_search_and_export_reject_unencodable_query_before_platform(tmp_path, action):
	"""搜索 / 导出必须在平台请求前拒绝，不能落成带磁盘指引的本地 IO 错误。"""
	context, platform = _context(tmp_path)
	inputs: dict[str, Any] = {"query": SURROGATE}
	if action == "candidate_export":
		inputs["output"] = str(tmp_path / "out.json")

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS[action](context, inputs, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert platform.calls == []
	assert not (tmp_path / "out.json").exists()


def test_execute_candidate_search_rejects_unencodable_query_before_pipeline():
	"""共享执行器是 search / export / watch run 的共同入口，校验放在这里。"""

	def pipeline(*args: Any, **kwargs: Any) -> Any:
		raise AssertionError("不可编码关键词不应进入搜索管线")

	with pytest.raises(WorkflowActionError) as exc_info:
		execute_candidate_search(
			object(), object(), Logger(), {"query": SURROGATE}, pipeline=pipeline
		)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "UTF-8" in str(exc_info.value)


def test_watch_run_legacy_bad_record_keeps_saved_search_invalid(tmp_path):
	"""旧版本或手工写入的坏记录仍在读取侧被拒绝，并给出删除重建指引。"""
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search("legacy", {"query": SURROGATE})

	context, _ = _context(tmp_path)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "name": "legacy"}, {})

	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "watch remove" in exc_info.value.recovery_action
