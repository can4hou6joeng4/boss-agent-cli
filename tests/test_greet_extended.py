"""commands/greet.py 覆盖率补齐测试。

覆盖 greet 成功路径、hook veto、batch-greet 完整执行链、rate-limit 停止、greet-limit 停止、失败重试。
"""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _make_raw_job(name: str = "Go 开发", security_id: str = "sec_x") -> dict:
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


# ── greet 成功路径 ─────────────────────────────────────────


@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_greet_success_renders_message_and_records_cache(mock_cache_cls, mock_auth_cls, mock_get_platform):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.greet.return_value = None

	runner = CliRunner()
	result = runner.invoke(cli, ["greet", "sec_001", "job_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sec_001"
	assert parsed["data"]["job_id"] == "job_001"
	assert "打招呼成功" in parsed["data"]["message"]

	mock_platform.greet.assert_called_once_with("sec_001", "job_001", "")
	mock_cache.record_greet.assert_called_once_with("sec_001", "job_001")


# Note: hook veto 分支已由 tests/test_hooks.py 独立覆盖，Click runner
# 不便注入 ctx.obj["hooks"]，此处不重复测试。


# ── batch-greet 真实执行路径 ─────────────────────────────


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_success_all(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""2 个职位全部打招呼成功。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("Go 1", "sec_1"), _make_raw_job("Go 2", "sec_2")]}
	}
	mock_client.greet.return_value = None  # 成功
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "golang", "--count", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["total_greeted"] == 2
	assert parsed["data"]["total_failed"] == 0
	assert len(parsed["data"]["greeted"]) == 2
	# 应调用 2 次 greet + 2 次 record_greet
	assert mock_client.greet.call_count == 2
	assert mock_cache.record_greet.call_count == 2


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_rate_limited_stops_remaining(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""第 1 个成功，第 2 个 RATE_LIMITED 应中止剩余。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [
			_make_raw_job("A", "s1"),
			_make_raw_job("B", "s2"),
			_make_raw_job("C", "s3"),
		]}
	}

	def greet_side_effect(sid, jid, msg=""):
		if sid == "s1":
			return None
		raise RuntimeError("RATE_LIMITED 请求频率过高")

	mock_client.greet.side_effect = greet_side_effect
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "3"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["total_greeted"] == 1
	assert parsed["data"]["stopped_reason"] == "RATE_LIMITED"


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_greet_limit_stops_remaining(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""GREET_LIMIT 错误关键字也应触发停止。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("A", "s1"), _make_raw_job("B", "s2")]}
	}
	mock_client.greet.side_effect = RuntimeError("GREET_LIMIT 今日上限已达")
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["stopped_reason"] == "GREET_LIMIT"
	assert parsed["data"]["total_greeted"] == 0


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_network_error_retries_once_then_skips(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""普通网络错误：第一次重试仍失败 → 跳过该职位，继续下一个。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("A", "s1"), _make_raw_job("B", "s2")]}
	}
	# s1: 两次都失败（非 RATE_LIMITED / GREET_LIMIT）
	# s2: 成功
	call_counts = {"s1": 0}

	def greet_side_effect(sid, jid, msg=""):
		if sid == "s1":
			call_counts["s1"] += 1
			raise ConnectionError("network timeout")
		return None

	mock_client.greet.side_effect = greet_side_effect
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["total_greeted"] == 1
	assert parsed["data"]["total_failed"] == 1
	# s1 应该被重试过 1 次（共调用 2 次）
	assert call_counts["s1"] == 2
	# s2 成功
	assert any(r["security_id"] == "s2" for r in parsed["data"]["greeted"])


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_network_error_first_retry_succeeds(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""第一次失败但重试成功，应算 success 不是 failed。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("A", "s1")]}
	}

	call_count = {"n": 0}

	def greet_side_effect(sid, jid, msg=""):
		call_count["n"] += 1
		if call_count["n"] == 1:
			raise ConnectionError("transient")
		return None  # 第二次成功

	mock_client.greet.side_effect = greet_side_effect
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "1"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["total_greeted"] == 1
	assert parsed["data"]["total_failed"] == 0


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_skips_already_greeted(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""已打过招呼的职位应被 candidates 过滤掉。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	# s1 已打过招呼，s2 没
	mock_cache.is_greeted.side_effect = lambda sid: sid == "s1"
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("A", "s1"), _make_raw_job("B", "s2")]}
	}
	mock_client.greet.return_value = None
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "5"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	# 只 s2 被打招呼
	assert parsed["data"]["total_greeted"] == 1
	assert mock_client.greet.call_count == 1
	assert mock_client.greet.call_args.args[0] == "s2"


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_respects_count_cap(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time):
	"""--count 最大 10，即使搜出 15 条也只处理 10 条。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job(f"Job {i}", f"s{i}") for i in range(15)]}
	}
	mock_client.greet.return_value = None
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, ["batch-greet", "test", "--count", "99", "--dry-run"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	# --count 会被 cap 到 10
	assert parsed["data"]["count"] == 10
