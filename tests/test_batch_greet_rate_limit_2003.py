"""batch-greet 限流停批的结果与信封契约（Issue #2003）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.claim_candidate_action.return_value = None
	return instance


def _job(name: str, security_id: str) -> dict:
	return {
		"encryptJobId": f"job_{security_id}",
		"jobName": name,
		"brandName": "TestCo",
		"salaryDesc": "20K",
		"cityName": "北京",
		"areaDistrict": "海淀区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang"],
		"welfareList": [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
	}


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_rate_limited_response_marks_candidate_failed_and_returns_error_envelope(
	mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, tmp_path
):
	"""响应形态的限流：触发停批的候选人进 failed，信封 ok:false（#2003）。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	client = _ctx_mock(mock_client_cls)
	client.search_jobs.return_value = {"zpData": {"jobList": [_job("A", "s1"), _job("B", "s2")]}}
	client.greet.return_value = {
		"code": 9,
		"__cli_error_details__": {"replay_safe": False, "__private": "ignored"},
	}
	client.is_success.side_effect = lambda response: response.get("code", 0) == 0
	client.parse_error.return_value = ("RATE_LIMITED", "请求频率过高")
	mock_time.sleep = MagicMock()

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "batch-greet", "test", "--count", "2"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "RATE_LIMITED"
	assert payload["error"]["recoverable"] is True
	failed = payload["error"]["details"]["failed"]
	assert [item["job_id"] for item in failed] == ["job_s1"]
	assert failed[0]["status"] == "failed"
	assert failed[0]["details"]["replay_safe"] is False
	assert "__private" not in failed[0]["details"]


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_rate_limited_exception_marks_candidate_failed(
	mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, tmp_path
):
	"""异常形态的限流：同样进 failed 且 ok:false（#2003）。"""

	class _RateLimited(Exception):
		code = "RATE_LIMITED"
		# 异常形态只有在能证明「请求未发出」时才按限流失败处理；否则走
		# 结果未知分支（既有契约）。
		details = {"request_sent": False}

	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	client = _ctx_mock(mock_client_cls)
	client.search_jobs.return_value = {"zpData": {"jobList": [_job("A", "s1")]}}
	client.greet.side_effect = _RateLimited("频率过高")
	mock_time.sleep = MagicMock()

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "batch-greet", "test", "--count", "1"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "RATE_LIMITED"
	assert [item["job_id"] for item in payload["error"]["details"]["failed"]] == ["job_s1"]


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_all_success_batch_still_ok_true(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, tmp_path):
	"""无停批的全成功批次仍是 ok:true（#2003 护栏）。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	client = _ctx_mock(mock_client_cls)
	client.search_jobs.return_value = {"zpData": {"jobList": [_job("A", "s1")]}}
	client.greet.return_value = {"code": 0, "zpData": {}}
	client.is_success.side_effect = lambda response: response.get("code", 0) == 0
	mock_time.sleep = MagicMock()

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "batch-greet", "test", "--count", "1"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["total_greeted"] == 1
	assert "stopped_reason" not in payload["data"]
