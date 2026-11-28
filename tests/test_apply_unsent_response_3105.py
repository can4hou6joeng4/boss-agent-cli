"""响应级「确定未发出」证据必须释放写预约（Issue #3105）。

浏览器客户端在写出前中断时会返回严格的 `__cli_error_details__.request_sent=False`；
apply / greet 不能把它当结果未知，否则预约没有 TTL、永久挡住同一职位。
全部离线：平台与登录态都是 mock。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.candidate_actions import classify_platform_response

_NOT_SENT: dict[str, Any] = {
	"code": -1,
	"message": "浏览器通道在请求发出前中断",
	"zpData": {},
	"__cli_error_code__": "NETWORK_ERROR",
	"__cli_error_details__": {"outcome": "not_sent", "request_sent": False, "replay_safe": True},
}


class _FakePlatform:
	name = "zhipin"

	def __init__(self, responses: list[dict[str, Any]]):
		self.responses = list(responses)
		self.apply_calls = 0
		self.greet_calls = 0

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def is_success(self, response):
		return response.get("code") in (0, 200)

	def parse_error(self, response):
		return response.get("__cli_error_code__", "UNKNOWN"), response.get("message", "")

	def unwrap_data(self, response):
		return response.get("zpData")

	def apply(self, security_id, job_id, lid=""):
		self.apply_calls += 1
		return self.responses.pop(0)

	def greet(self, security_id, job_id):
		self.greet_calls += 1
		return self.responses.pop(0)

	def search_jobs(self, query, **kwargs):
		return {
			"code": 0,
			"zpData": {
				"hasMore": False,
				"jobList": [{
					"encryptJobId": "job-x",
					"securityId": "sid-x",
					"jobName": "职位X",
					"cityName": "广州",
				}],
			}
		}


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _claims(tmp_path: Path) -> list[tuple[Any, ...]]:
	db = tmp_path / "cache" / "boss_agent.db"
	with sqlite3.connect(db) as conn:
		return conn.execute("SELECT job_id, action, status FROM candidate_action_claims").fetchall()


def test_classifier_only_trusts_strict_request_sent_false() -> None:
	platform = _FakePlatform([])
	assert classify_platform_response(platform, _NOT_SENT) == "retryable"

	no_evidence = {"code": -1, "__cli_error_code__": "NETWORK_ERROR"}
	assert classify_platform_response(platform, no_evidence) == "unknown"

	for value in (True, 0, "false", None):
		response = {**_NOT_SENT, "__cli_error_details__": {"request_sent": value}}
		assert classify_platform_response(platform, response) == "unknown"


@patch("boss_agent_cli.commands.apply.require_compliance_allowed", return_value=True)
@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
def test_apply_releases_claim_on_provably_unsent_response(
	mock_auth_cls, mock_platform_cls, mock_compliance, tmp_path
) -> None:
	platform = _FakePlatform([dict(_NOT_SENT), {"code": 0, "zpData": {}}])
	mock_platform_cls.return_value.__enter__.return_value = platform

	failed = _invoke(tmp_path, "apply", "sid-x", "job-x")
	assert failed.exit_code == 1, failed.output
	error = json.loads(failed.output)["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["details"]["request_sent"] is False
	assert not error["details"].get("outcome_unknown")
	assert _claims(tmp_path) == []

	succeeded = _invoke(tmp_path, "apply", "sid-x", "job-x")
	assert succeeded.exit_code == 0, succeeded.output
	assert json.loads(succeeded.output)["ok"] is True
	assert platform.apply_calls == 2
	assert _claims(tmp_path) == [("job-x", "apply", "sent")]


@patch("boss_agent_cli.commands.greet.time.sleep")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
def test_batch_greet_records_provably_unsent_as_failed_not_unconfirmed(
	mock_auth_cls, mock_platform_cls, mock_sleep, tmp_path
) -> None:
	platform = _FakePlatform([dict(_NOT_SENT), dict(_NOT_SENT)])
	mock_platform_cls.return_value.__enter__.return_value = platform

	result = _invoke(tmp_path, "batch-greet", "Go", "--count", "1")

	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	assert [item["status"] for item in data["failed"]] == ["failed"]
	assert data.get("unconfirmed", []) == []
	assert platform.greet_calls == 2
	assert _claims(tmp_path) == []
