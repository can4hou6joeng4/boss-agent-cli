"""向导 search / export 支持 search_url 与 preset（Issue #2685）。

CLI `boss search` 允许用 `--url` / `--preset` 代替关键词，`boss export` 允许
`--url`；向导 / MCP 此前只认 `query`，把这两条入口挡在 plan 层。这里覆盖展开
口径：显式输入优先、URL 的 page 仅在未显式给出时采用、坏 preset 与非法 URL
在平台请求前按参数错误拒绝。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan, catalog_data
from boss_agent_cli.wizard.models import WizardInput, WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError

_URL = "https://www.zhipin.com/web/geek/job?query=Python&city=101010100&page=2"
_URL_NO_QUERY = "https://www.zhipin.com/web/geek/job?city=101010100"


class _Platform:
	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


class _Result:
	items: list[dict[str, Any]] = []
	has_more_reported: bool | None = False
	total = 0


def _context(tmp_path: Path, *, forbid_platform: bool = False) -> ActionContext:
	def factory(*args: object, **kwargs: object) -> _Platform:
		if forbid_platform:
			raise AssertionError("非法输入不应构造平台实例")
		return _Platform()

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def _capture_search(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
	captured: dict[str, Any] = {}

	def fake(platform: Any, cache: Any, logger: Any, inputs: dict[str, Any], **kwargs: Any) -> _Result:
		captured["inputs"] = dict(inputs)
		return _Result()

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", fake)
	return captured


def _save_preset(tmp_path: Path, name: str, params: dict[str, Any]) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.save_saved_search(name, params)


# ── candidate_search：search_url ──────────────────────────────────


def test_search_url_supplies_query_filters_and_page(tmp_path, monkeypatch):
	captured = _capture_search(monkeypatch)

	data = DEFAULT_ACTIONS["candidate_search"](_context(tmp_path), {"search_url": _URL}, {}).data

	assert captured["inputs"]["query"] == "Python"
	assert captured["inputs"]["raw_params"] == {"city": "101010100"}
	assert captured["inputs"]["page"] == 2
	# 回显的分页证据与 URL 页码同口径，Agent 才能据此翻下一页。
	assert data["pagination"]["page"] == 2


def test_explicit_query_and_page_override_url(tmp_path, monkeypatch):
	captured = _capture_search(monkeypatch)

	DEFAULT_ACTIONS["candidate_search"](
		_context(tmp_path),
		{"search_url": _URL, "query": "Golang", "page": 5},
		{},
	)

	assert captured["inputs"]["query"] == "Golang"
	assert captured["inputs"]["page"] == 5


def test_explicit_raw_params_override_url_params(tmp_path, monkeypatch):
	# CLI 的显式选项在链接参数之后覆盖（`search.py` 先并入链接参数，再用显式
	# code 参数 update）：向导显式 raw_params 同口径优先于链接（Issue #2690）。
	captured = _capture_search(monkeypatch)

	DEFAULT_ACTIONS["candidate_search"](
		_context(tmp_path),
		{"search_url": _URL, "raw_params": {"city": "101020100"}},
		{},
	)

	assert captured["inputs"]["raw_params"] == {"city": "101020100"}


def test_url_params_kept_when_not_explicitly_overridden(tmp_path, monkeypatch):
	# 显式覆盖只作用于同名键：链接独有的参数仍要保留，否则显式给一个无关
	# raw_params 会把链接的筛选条件整体清掉（Issue #2690）。
	captured = _capture_search(monkeypatch)

	DEFAULT_ACTIONS["candidate_search"](
		_context(tmp_path),
		{"search_url": _URL, "raw_params": {"jobType": "1902"}},
		{},
	)

	assert captured["inputs"]["raw_params"] == {"jobType": "1902", "city": "101010100"}


def test_url_without_query_is_accepted(tmp_path, monkeypatch):
	# `export --url` / `search --url` 的链接可以只带筛选参数：CLI 此时按空
	# 关键词 + 筛选搜索，向导不能因为 query 缺失报 WIZARD_INPUT_REQUIRED。
	captured = _capture_search(monkeypatch)

	DEFAULT_ACTIONS["candidate_search"](_context(tmp_path), {"search_url": _URL_NO_QUERY}, {})

	assert captured["inputs"]["query"] == ""
	assert captured["inputs"]["raw_params"] == {"city": "101010100"}


@pytest.mark.parametrize(
	"search_url",
	["https://example.com/web/geek/job?query=Python", "not-a-url", "https://www.zhipin.com/web/other?query=x"],
)
def test_invalid_search_url_rejected_before_platform(tmp_path, monkeypatch, search_url):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, forbid_platform=True), {"search_url": search_url}, {}
		)

	assert caught.value.code == "INVALID_PARAM"


def test_blank_search_url_rejected_before_platform(tmp_path, monkeypatch):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, forbid_platform=True), {"search_url": "   "}, {}
		)

	assert caught.value.code == "INVALID_PARAM"


# ── candidate_search：preset ──────────────────────────────────────


def test_preset_fills_missing_keys_and_explicit_input_wins(tmp_path, monkeypatch):
	_save_preset(tmp_path, "daily", {"query": "Go", "city": "上海", "salary": "20-50K"})
	captured = _capture_search(monkeypatch)

	DEFAULT_ACTIONS["candidate_search"](_context(tmp_path), {"preset": "daily", "city": "北京"}, {})

	assert captured["inputs"]["query"] == "Go"
	assert captured["inputs"]["city"] == "北京"
	assert captured["inputs"]["salary"] == "20-50K"


def test_missing_preset_reports_not_found(tmp_path, monkeypatch):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, forbid_platform=True), {"preset": "ghost"}, {}
		)

	assert caught.value.code == "SAVED_SEARCH_NOT_FOUND"
	assert "ghost" in str(caught.value)


def test_corrupt_preset_reports_saved_search_invalid(tmp_path, monkeypatch):
	# 空白关键词是旧版本 / 手工编辑可能留下的存量记录（Issue #1798、#2529）。
	_save_preset(tmp_path, "broken", {"query": "   "})
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, forbid_platform=True), {"preset": "broken"}, {}
		)

	assert caught.value.code == "SAVED_SEARCH_INVALID"
	assert "preset remove" in caught.value.recovery_action


def test_blank_preset_rejected_before_platform(tmp_path, monkeypatch):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](
			_context(tmp_path, forbid_platform=True), {"preset": "  "}, {}
		)

	assert caught.value.code == "INVALID_PARAM"


def test_search_without_any_source_still_requires_query(tmp_path, monkeypatch):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_search"](_context(tmp_path, forbid_platform=True), {}, {})

	assert caught.value.code == "WIZARD_INPUT_REQUIRED"


# ── candidate_export：search_url ──────────────────────────────────


def test_export_accepts_url_without_query(tmp_path, monkeypatch):
	captured = _capture_search(monkeypatch)

	result = DEFAULT_ACTIONS["candidate_export"](
		_context(tmp_path),
		{"search_url": _URL, "output": str(tmp_path / "out.json")},
		{},
	)

	assert captured["inputs"]["query"] == "Python"
	assert captured["inputs"]["raw_params"] == {"city": "101010100"}
	assert Path(result.data["path"]) == tmp_path / "out.json"


def test_export_requires_query_or_url(tmp_path, monkeypatch):
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path, forbid_platform=True),
			{"output": str(tmp_path / "out.json")},
			{},
		)

	assert caught.value.code == "WIZARD_INPUT_REQUIRED"


def test_export_still_rejects_blank_query_without_url(tmp_path, monkeypatch):
	# 回归 #2519：空白关键词不能因为「有输出路径」就按无关键词导出。
	_capture_search(monkeypatch)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path, forbid_platform=True),
			{"query": "   ", "output": str(tmp_path / "out.json")},
			{},
		)

	assert caught.value.code == "INVALID_PARAM"
	assert "query" in str(caught.value)


# ── plan / catalog ────────────────────────────────────────────────


@pytest.mark.parametrize("inputs", [{"search_url": _URL}, {"preset": "daily"}])
def test_build_plan_accepts_url_or_preset_for_job_search(inputs):
	plan = build_plan(
		WizardInput.from_mapping(
			{"role": "candidate", "platform": "zhipin", "goal": "job_search", "inputs": inputs}
		)
	)

	assert plan.goal == "job_search"


def test_build_plan_rejects_job_search_without_any_source():
	with pytest.raises(WorkflowInputError, match="缺少 inputs"):
		build_plan(
			WizardInput.from_mapping(
				{"role": "candidate", "platform": "zhipin", "goal": "job_search", "inputs": {}}
			)
		)


def test_catalog_exposes_either_inputs():
	goals = catalog_data()["roles"]["candidate"]["goals"]

	assert goals["job_search"]["either_inputs"] == [["query", "search_url", "preset"]]
	assert goals["export"]["either_inputs"] == [["query", "search_url"]]
	# 其余 goal 没有「要么输入」，保持空数组而不是缺键。
	assert goals["job_detail"]["either_inputs"] == []
