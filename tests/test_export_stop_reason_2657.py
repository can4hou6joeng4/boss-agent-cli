"""导出场景的翻页终止原因与 hasMore 缺失口径（Issue #2657）。

顶层 `boss export` 把缺失 / 非布尔的 ``hasMore`` 当成「未知」继续按预算
扫描（Issue #903）；向导 export 此前沿用搜索管线的保守口径，只导出首屏，
且 ``truncated`` 用一个被压成 False 的 ``has_more`` 计算。这里覆盖管线
新参数、终止原因，以及共享执行器与 action 的透传。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import SearchFilterCriteria, run_search_pipeline
from boss_agent_cli.wizard.actions import execute_candidate_search


class _FakeLogger:
	def info(self, message: str) -> None:
		pass


class _FakeCache:
	def is_greeted(self, security_id: str = "", *, job_id: str = "", account: str = "") -> bool:
		return False

	def get_job_desc(self, security_id: str) -> str | None:
		return None

	def put_job_desc(self, security_id: str, description: str) -> None:
		pass


class _FakeClient:
	name = "zhipin"

	def __init__(self, pages: list[dict[str, Any]]) -> None:
		self.pages = list(pages)
		self.search_calls: list[dict[str, Any]] = []

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		self.search_calls.append({"query": query, "filters": filters})
		return self.pages.pop(0)

	def is_success(self, response: dict[str, Any]) -> bool:
		return True

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData")


def _job(security_id: str, job_id: str) -> dict[str, Any]:
	return {
		"encryptJobId": job_id,
		"jobName": f"Job-{job_id}",
		"brandName": f"Company-{job_id}",
		"salaryDesc": "20-30K",
		"cityName": "广州",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"welfareList": [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李女士",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
		"lid": "",
	}


def _criteria() -> SearchFilterCriteria:
	return SearchFilterCriteria(query="Go")


def test_pipeline_stops_on_missing_has_more_by_default():
	"""默认（搜索类）口径不变：缺失 hasMore 立即停止并记 has_more_unknown。"""
	client = _FakeClient([
		{"zpData": {"jobList": [_job("sec-a", "job-a")]}},
		{"zpData": {"hasMore": False, "jobList": [_job("sec-b", "job-b")]}},
	])

	result = run_search_pipeline(
		client, _FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=3,
	)

	assert [item["job_id"] for item in result.items] == ["job-a"]
	assert result.has_more is False
	assert result.has_more_reported is None
	assert result.stop_reason == "has_more_unknown"
	assert len(client.search_calls) == 1


def test_export_pipeline_continues_when_has_more_missing():
	"""导出口径：缺失 hasMore 不等于没有下一页，按预算继续扫描。"""
	client = _FakeClient([
		{"zpData": {"jobList": [_job("sec-a", "job-a")]}},
		{"zpData": {"jobList": [_job("sec-b", "job-b")]}},
		{"zpData": {"hasMore": False, "jobList": [_job("sec-c", "job-c")]}},
	])

	result = run_search_pipeline(
		client, _FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=5,
		continue_when_has_more_unknown=True,
	)

	assert [item["job_id"] for item in result.items] == ["job-a", "job-b", "job-c"]
	assert result.has_more_reported is False
	assert result.stop_reason == "has_more_false"
	assert len(client.search_calls) == 3


def test_export_pipeline_budget_exhausted_with_unknown_has_more():
	"""预算耗尽且平台始终没说没有下一页：终止原因必须是预算，而不是完整。"""
	client = _FakeClient([
		{"zpData": {"jobList": [_job("sec-a", "job-a")]}},
		{"zpData": {"jobList": [_job("sec-b", "job-b")]}},
	])

	result = run_search_pipeline(
		client, _FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=2,
		continue_when_has_more_unknown=True, limit=10,
	)

	assert len(result.items) == 2
	assert result.has_more_reported is None
	assert result.stop_reason == "page_budget_exhausted"


def test_pipeline_reports_limit_empty_and_explicit_false_reasons():
	"""limit / 空页 / 平台显式 false 各自对应确定的终止原因。"""
	limit_result = run_search_pipeline(
		_FakeClient([{"zpData": {"hasMore": True, "jobList": [_job("sec-a", "job-a")]}}]),
		_FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=3, limit=1,
	)
	assert limit_result.stop_reason == "limit_reached"

	empty_result = run_search_pipeline(
		_FakeClient([{"zpData": {"hasMore": True, "jobList": []}}]),
		_FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=3,
	)
	assert empty_result.stop_reason == "empty_page"

	false_result = run_search_pipeline(
		_FakeClient([{"zpData": {"hasMore": False, "jobList": [_job("sec-a", "job-a")]}}]),
		_FakeCache(), _FakeLogger(), criteria=_criteria(), max_pages=3,
	)
	assert false_result.stop_reason == "has_more_false"


def test_execute_candidate_search_forwards_continue_flag_to_pipeline():
	captured: dict[str, Any] = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return object()

	execute_candidate_search(
		object(),
		object(),
		Logger(),
		{"query": "Go"},
		pipeline=pipeline,
		limit=5,
		continue_when_has_more_unknown=True,
	)

	assert captured["continue_when_has_more_unknown"] is True


def test_execute_candidate_search_omits_continue_flag_by_default():
	"""搜索类调用不向测试替身传入新关键字，保持既有签名兼容。"""
	captured: dict[str, Any] = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return object()

	execute_candidate_search(object(), object(), Logger(), {"query": "Go"}, pipeline=pipeline)

	assert "continue_when_has_more_unknown" not in captured


def test_export_action_takes_count_past_missing_has_more(tmp_path, monkeypatch):
	"""端到端：第 1 页缺 hasMore 时，count 导出仍能取满目标条数。

	修复前管线首屏即停，count=2 只导出 1 条且 truncated=false，调用方
	会把前缀当成完整结果（Issue #2657）。
	"""
	import boss_agent_cli.wizard.actions as actions

	class _Platform:
		def __enter__(self) -> "_Platform":
			return self

		def __exit__(self, *args: object) -> None:
			return None

	context = actions.ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args, **kwargs: _Platform(),
	)
	client = _FakeClient([
		{"zpData": {"jobList": [_job("sec-a", "job-a")]}},
		{"zpData": {"hasMore": True, "jobList": [_job("sec-b", "job-b"), _job("sec-c", "job-c")]}},
	])

	def real_pipeline(platform, cache, logger, **kwargs):
		return run_search_pipeline(client, _FakeCache(), _FakeLogger(), **kwargs)

	real_execute = actions.execute_candidate_search

	def wrapper(platform, cache, logger, inputs, *, limit=None, continue_when_has_more_unknown=False):
		return real_execute(
			platform,
			cache,
			logger,
			inputs,
			pipeline=real_pipeline,
			limit=limit,
			continue_when_has_more_unknown=continue_when_has_more_unknown,
		)

	monkeypatch.setattr(actions, "execute_candidate_search", wrapper)
	step = actions.DEFAULT_ACTIONS["candidate_export"](
		context,
		{"query": "Go", "count": 2, "output": str(tmp_path / "out.json")},
		{},
	)

	assert step.data["count"] == 2
	assert step.data["requested"] == 2
	assert step.data["stop_reason"] == "count_reached"
	assert step.data["truncated"] is False
	assert len(client.search_calls) == 2
