"""向导 recommendations 的 with_score 输入与 CLI 共用归一 / 评分实现（Issue #2662）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError

_JOBS = [
	{
		"encryptJobId": "job-1",
		"securityId": "sid-1",
		"lid": "lid-1",
		"jobName": "Golang 工程师",
		"salaryDesc": "30-50K",
		"cityName": "上海",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"welfareList": ["五险一金"],
	},
	{
		"encryptJobId": "job-2",
		"securityId": "sid-2",
		"lid": "lid-2",
		"jobName": "Python 工程师",
		"salaryDesc": "10-20K",
		"cityName": "北京",
	},
]


class _Platform:
	def __init__(
		self,
		*,
		jobs: list[dict[str, Any]] | None = None,
		expect: dict[str, Any] | None = None,
		expect_response: dict[str, Any] | None = None,
		expect_error: Exception | None = None,
	) -> None:
		self._jobs = _JOBS if jobs is None else jobs
		self._expect = {} if expect is None else expect
		self._expect_response = expect_response
		self._expect_error = expect_error
		self.recommend_calls: list[int] = []
		self.expect_calls = 0

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def resume_expect(self) -> dict[str, Any]:
		self.expect_calls += 1
		if self._expect_error is not None:
			raise self._expect_error
		if self._expect_response is not None:
			return self._expect_response
		return {"code": 0, "zpData": self._expect}

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		self.recommend_calls.append(page)
		return {"code": 0, "zpData": {"hasMore": False, "jobList": self._jobs}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "ACCOUNT_RISK", "访问环境异常"


def _context(tmp_path, factory) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def _run(tmp_path, platform: _Platform, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS["candidate_recommend"](_context(tmp_path, lambda *a, **k: platform), inputs, {}).data)


@pytest.mark.parametrize("inputs", [{}, {"with_score": None}, {"with_score": False}])
def test_recommend_default_keeps_raw_items_without_resume_expect(tmp_path, inputs):
	platform = _Platform()

	data = _run(tmp_path, platform, {**inputs, "page": 1})

	assert platform.expect_calls == 0
	assert platform.recommend_calls == [1]
	# 缺省保持现状：平台原始行原样透传，不追加归一字段。
	assert data["items"] == _JOBS
	assert data["result"]["jobList"] == _JOBS


def test_recommend_with_score_attaches_scores_and_greeted(tmp_path):
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.record_greet("sid-1", "job-1")
	platform = _Platform(expect={"cityName": "上海", "salaryDesc": "30-50K", "degree": "本科"})

	data = _run(tmp_path, platform, {"with_score": True})

	assert platform.expect_calls == 1
	assert platform.recommend_calls == [1]
	items = data["items"]
	assert [item["job_id"] for item in items] == ["job-1", "job-2"]
	first, second = items
	# 与 CLI 同口径：归一成 JobItem 字段并按当前账号计算 greeted。
	assert first["security_id"] == "sid-1"
	assert first["lid"] == "lid-1"
	assert first["greeted"] is True
	assert second["greeted"] is False
	assert "encryptJobId" not in first
	assert isinstance(first["match_score"], int) and first["match_score"] > 0
	assert "城市匹配" in first["match_reasons"]
	assert isinstance(first["mismatch_reasons"], list)
	# `result` 保留平台原始响应，Agent 仍能读到未归一行。
	assert data["result"]["jobList"] == _JOBS


def test_recommend_with_score_deduplicates_job_list(tmp_path):
	duplicated = [
		{"encryptJobId": "job-1", "securityId": "sid-first", "lid": "lid-first", "jobName": "职位甲"},
		{"encryptJobId": "job-1", "securityId": "sid-rotated", "lid": "lid-rotated", "jobName": "职位甲"},
	]
	platform = _Platform(jobs=duplicated)

	data = _run(tmp_path, platform, {"with_score": True})

	assert len(data["items"]) == 1
	assert (data["items"][0]["security_id"], data["items"][0]["lid"]) == ("sid-first", "lid-first")
	assert len(data["result"]["jobList"]) == 2


def test_recommend_with_score_tolerates_non_object_expect_data(tmp_path):
	platform = _Platform(expect_response={"code": 0, "zpData": ["上海"]})

	data = _run(tmp_path, platform, {"with_score": True})

	# 期望结构漂移时按空期望评分，不让整页推荐失败；福利维度仍计分。
	assert [item["job_id"] for item in data["items"]] == ["job-1", "job-2"]
	assert all(isinstance(item["match_score"], int) for item in data["items"])
	assert "城市匹配" not in data["items"][0]["match_reasons"]


def test_recommend_resume_expect_failure_classified_before_list(tmp_path):
	platform = _Platform(expect_response={"code": 37})

	with pytest.raises(WorkflowActionError) as exc_info:
		_run(tmp_path, platform, {"with_score": True})

	assert exc_info.value.code == "ACCOUNT_RISK"
	assert exc_info.value.recoverable is False
	assert platform.recommend_calls == []


def test_recommend_not_supported_skips_list_request(tmp_path):
	platform = _Platform(expect_error=NotImplementedError(""))

	with pytest.raises(WorkflowActionError) as exc_info:
		_run(tmp_path, platform, {"with_score": True})

	assert exc_info.value.code == "NOT_SUPPORTED"
	assert str(exc_info.value) == "当前平台不支持求职期望能力"
	assert exc_info.value.recoverable is True
	assert platform.recommend_calls == []


@pytest.mark.parametrize("value", ["true", 1, [], {"enabled": True}])
def test_recommend_rejects_non_bool_with_score_before_platform(tmp_path, value):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非布尔 with_score 不应打开平台或发起推荐请求")

	context = _context(tmp_path, must_not_open)
	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_recommend"](context, {"with_score": value}, {})
