"""向导 / MCP 的 welfare 输入形态（Issue #2654）。

搜索 / 导出共用的 ``welfare`` 数组入口此前用 ``str(item)`` 归一元素，
数字与嵌套容器会变成永不命中的标签；``false`` / ``0`` 还会被真值判断
当成「没给」。watch add 的同类入口已在 #2421 收紧，这里钉住搜索 / 导出。
"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import resolve_welfare_keywords
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_candidate_search,
)
from boss_agent_cli.wizard.runner import WorkflowActionError


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


def _capture(**inputs: Any) -> Any:
	captured: dict[str, Any] = {}
	sentinel = object()

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return sentinel

	assert execute_candidate_search(None, None, None, {"query": "go", **inputs}, pipeline=pipeline) is sentinel
	return captured["welfare_conditions"]


@pytest.mark.parametrize("welfare", [[123], ["双休", ["五险一金"]], [None]])
def test_search_rejects_non_string_welfare_elements(welfare):
	"""数组元素经 str() 会变成 "123" / "['双休']" 这类永不命中的标签。"""
	def pipeline(*args: Any, **kwargs: Any) -> Any:
		raise AssertionError("非法 welfare 不应进入搜索管线")

	with pytest.raises(WorkflowActionError) as exc_info:
		execute_candidate_search(None, None, None, {"query": "go", "welfare": welfare}, pipeline=pipeline)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "welfare 的第" in str(exc_info.value)
	assert "必须是字符串" in str(exc_info.value)


@pytest.mark.parametrize("welfare", [False, 0, {"双休": ["周末双休"]}])
def test_search_rejects_non_string_non_array_welfare(welfare):
	"""显式 false / 0 / 对象不能经真值判断静默变成「不筛选」。"""
	def pipeline(*args: Any, **kwargs: Any) -> Any:
		raise AssertionError("非法 welfare 不应进入搜索管线")

	with pytest.raises(WorkflowActionError) as exc_info:
		execute_candidate_search(None, None, None, {"query": "go", "welfare": welfare}, pipeline=pipeline)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "字符串或字符串数组" in str(exc_info.value)


def test_search_strips_string_array_welfare_labels():
	assert _capture(welfare=["双休", " 五险一金 "]) == [
		("双休", resolve_welfare_keywords("双休")),
		("五险一金", resolve_welfare_keywords("五险一金")),
	]


def test_search_keeps_empty_string_and_empty_array_as_no_filter():
	assert _capture(welfare="") is None
	assert _capture(welfare=[]) == []


@pytest.mark.parametrize("action", ["candidate_search", "candidate_export"])
def test_actions_reject_malformed_welfare_before_platform(tmp_path, action):
	"""搜索 / 导出动作在平台请求与写文件之前拒绝，不消耗受限请求。"""
	context, platform = _context(tmp_path)
	inputs: dict[str, Any] = {"query": "Python", "welfare": [123]}
	if action == "candidate_export":
		inputs["output"] = str(tmp_path / "out.json")

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS[action](context, inputs, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert platform.calls == []
	assert not (tmp_path / "out.json").exists()
