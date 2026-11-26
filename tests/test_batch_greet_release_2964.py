"""batch-greet 释放本地预约失败时不能报「结果未知」（Issue #2964）。

平台明确拒绝（限流 / 重试耗尽的业务失败）或请求可证明未发出时，请求本身
没有产生平台记录；此时本地 pending 预约删不掉只是本地状态问题。旧实现让
释放异常落进通用 ``except Exception``，按 ``ACTION_UNCONFIRMED`` 收尾，或在
重试耗尽分支直接冒成 ``INTERNAL_ERROR``。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli

JOBS = [
	{
		"encryptJobId": "job-1",
		"securityId": "sec-1",
		"jobName": "后端开发",
		"brandName": "示例公司",
		"salaryDesc": "20K",
		"cityName": "北京",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"bossName": "招聘者",
		"bossTitle": "HR",
	},
	{
		"encryptJobId": "job-2",
		"securityId": "sec-2",
		"jobName": "前端开发",
		"brandName": "示例公司",
		"salaryDesc": "18K",
		"cityName": "北京",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"bossName": "招聘者",
		"bossTitle": "HR",
	},
]


class _TypedRateLimit(httpx.ConnectError):
	"""可证明未发出、且带限流错误码的 typed 异常（覆盖批量循环的 typed 分支）。"""

	code = "RATE_LIMITED"


class _Platform:
	name = "zhipin"

	def __init__(
		self, greet: Any, *, parse_error: tuple[str, str] = ("RATE_LIMITED", "too fast")
	) -> None:
		self._greet = greet
		self._parse_error = parse_error
		self.greet_calls: list[str] = []

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def search_jobs(self, query: str, **kwargs: Any) -> dict[str, Any]:
		return {"code": 0, "zpData": {"jobList": [dict(job) for job in JOBS]}}

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		self.greet_calls.append(job_id)
		if isinstance(self._greet, BaseException):
			raise self._greet
		return dict(self._greet)

	@staticmethod
	def is_success(response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return self._parse_error

	@staticmethod
	def unwrap_data(response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}


def _broken_release(*args: Any, **kwargs: Any) -> None:
	raise sqlite3.OperationalError("database is locked")


def _invoke(platform: _Platform, tmp_path: Path, *, release_broken: bool) -> Any:
	with ExitStack() as stack:
		stack.enter_context(patch("boss_agent_cli.commands.greet.AuthManager"))
		stack.enter_context(patch("boss_agent_cli.commands.greet.get_platform_instance", return_value=platform))
		if release_broken:
			stack.enter_context(
				patch("boss_agent_cli.commands.greet.release_candidate_action", side_effect=_broken_release)
			)
		return CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "batch-greet", "后端", "--count", "2"]
		)


def _assert_local_state_error(result: Any) -> dict[str, Any]:
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	error = envelope["error"]
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	details = error["details"]
	assert details["request_sent"] is False
	assert details["recorded"] is False
	assert details["replay_safe"] is False
	assert not details.get("outcome_unknown")
	assert details["unreleased"]["job_id"] == "job-1"
	assert details["release_error"] == "OperationalError"
	return envelope


def _assert_pending(tmp_path: Path, job_id: str, security_id: str) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.claim_candidate_action(security_id, job_id, "greet") == "pending"
		# claim 会补一条 pending；校验完删掉，避免影响后续断言。
		assert cache.release_candidate_action(job_id, "greet") is True


def test_rate_limited_response_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _Platform({"code": 9, "message": "too fast"})

	result = _invoke(platform, tmp_path, release_broken=True)

	envelope = _assert_local_state_error(result)
	assert envelope["error"]["details"]["failed"][0]["error"] == "RATE_LIMITED"
	# 剩下一个候选人不能被发送。
	assert platform.greet_calls == ["job-1"]
	_assert_pending(tmp_path, "job-1", "sec-1")


def test_typed_rate_limit_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _Platform(_TypedRateLimit("too fast"))

	result = _invoke(platform, tmp_path, release_broken=True)

	_assert_local_state_error(result)
	assert platform.greet_calls == ["job-1"]
	_assert_pending(tmp_path, "job-1", "sec-1")


def test_retry_exhausted_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _Platform({"code": 1, "message": "bad request"}, parse_error=("INVALID_PARAM", "bad"))

	with patch("boss_agent_cli.commands.greet.time.sleep"):
		result = _invoke(platform, tmp_path, release_broken=True)

	_assert_local_state_error(result)
	# 业务失败重试一次后仍失败，不会再发给第二个候选人。
	assert platform.greet_calls == ["job-1", "job-1"]
	_assert_pending(tmp_path, "job-1", "sec-1")


def test_request_stop_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _Platform(RequestBudgetExceeded("no slot"))

	result = _invoke(platform, tmp_path, release_broken=True)

	_assert_local_state_error(result)
	assert platform.greet_calls == ["job-1"]
	_assert_pending(tmp_path, "job-1", "sec-1")


def test_request_stop_release_failure_marks_candidate_failed(tmp_path: Path) -> None:
	"""请求停止点释放失败时，已尝试的候选人必须出现在 failed 里（Issue #2972）。"""
	platform = _Platform(RequestBudgetExceeded("no slot"))

	result = _invoke(platform, tmp_path, release_broken=True)

	envelope = _assert_local_state_error(result)
	failed = envelope["error"]["details"]["failed"]
	assert [item["job_id"] for item in failed] == ["job-1"]
	assert failed[0]["status"] == "failed"
	assert failed[0]["error"] == "REQUEST_BUDGET_EXHAUSTED"
	assert failed[0]["details"] == {"release_failed": True}


def test_rate_limited_with_working_release_stays_rate_limited(tmp_path: Path) -> None:
	platform = _Platform({"code": 9, "message": "too fast"})

	result = _invoke(platform, tmp_path, release_broken=False)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "RATE_LIMITED"
	assert envelope["error"]["recoverable"] is True
	assert platform.greet_calls == ["job-1"]
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		# 释放成功：再次 claim 得到的是新预约（不是旧 pending 挡住）。
		assert cache.claim_candidate_action("sec-1", "job-1", "greet") is None
