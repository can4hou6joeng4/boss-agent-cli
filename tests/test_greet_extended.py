"""commands/greet.py 覆盖率补齐测试。

覆盖 greet 成功路径、hook veto、batch-greet 完整执行链、rate-limit 停止、greet-limit 停止、失败重试。
"""

import json
import shlex
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.error_codes import ERROR_CODES


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
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
def test_greet_success_renders_message_and_records_cache(mock_cache_cls, mock_auth_cls, mock_get_platform, legacy_args):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.greet.return_value = {"code": 0, "zpData": {}}
	mock_platform.is_success.return_value = True

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "greet", "sec_001", "job_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sec_001"
	assert parsed["data"]["job_id"] == "job_001"
	assert "打招呼成功" in parsed["data"]["message"]

	mock_platform.greet.assert_called_once_with("sec_001", "job_001", "")
	mock_cache.record_greet.assert_called_once_with("sec_001", "job_001", account="")


@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
@patch("boss_agent_cli.main.create_hook_bus")
def test_greet_hook_veto_returns_registered_recovery_action(
	mock_hook_bus, mock_cache_cls, mock_auth_cls, mock_get_platform, legacy_args
):
	"""钩子阻止信封必须带登记表的 recovery_action，而不是 null。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_hook_bus.return_value.greet_before.call.return_value = "company blocked"

	result = CliRunner().invoke(cli, [*legacy_args, "greet", "sec_001", "job_001"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "HOOK_BLOCKED"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"] == ERROR_CODES["HOOK_BLOCKED"]["recovery_action"]
	mock_platform.greet.assert_not_called()


# ── batch-greet 真实执行路径 ─────────────────────────────


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_success_all(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args):
	"""2 个职位全部打招呼成功。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [_make_raw_job("Go 1", "sec_1"), _make_raw_job("Go 2", "sec_2")]}
	}
	mock_client.greet.return_value = {"code": 0, "zpData": {}}
	mock_client.is_success.return_value = True
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "golang", "--count", "2"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["total_greeted"] == 2
	assert parsed["data"]["total_failed"] == 0
	assert len(parsed["data"]["greeted"]) == 2
	# 应调用 2 次 greet + 2 次 record_greet
	assert mock_client.greet.call_count == 2
	assert mock_cache.record_greet.call_count == 2
	# 招呼发完的下一步是跟进会话（`me` 没有 greet 分区）。
	assert [shlex.split(action) for action in parsed["hints"]["next_actions"]] == [["boss", *legacy_args, "chat"]]


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_rate_limited_stops_remaining(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args):
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
			return {"code": 0, "zpData": {}}
		raise RuntimeError("RATE_LIMITED 请求频率过高")

	mock_client.greet.side_effect = greet_side_effect
	mock_client.is_success.return_value = True
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "test", "--count", "3"])
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
	mock_client.is_success.return_value = True
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
		return {"code": 0, "zpData": {}}

	mock_client.greet.side_effect = greet_side_effect
	mock_client.is_success.return_value = True
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
		return {"code": 0, "zpData": {}}  # 第二次成功

	mock_client.greet.side_effect = greet_side_effect
	mock_client.is_success.return_value = True
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
	# s1 已打过招呼，s2 没（job_id 是关键参数，仍需接受）
	mock_cache.is_greeted.side_effect = lambda sid, *, job_id="", account="": sid == "s1"
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


# ── batch-greet 风控终止语义（Issue #419） ─────────────────


def _three_jobs_client(mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"jobList": [
			_make_raw_job("A", "s1"),
			_make_raw_job("B", "s2"),
			_make_raw_job("C", "s3"),
		]}
	}
	return mock_client


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_account_risk_exception_stops_without_retry(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args):
	"""异常形态的 code 36：第 2 个命中风控后立即停批，不重试、不继续第 3 个，信封 ok:false。"""
	from boss_agent_cli.api.client import AccountRiskError

	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _three_jobs_client(mock_client_cls)

	def greet_side_effect(sid, jid, msg=""):
		if sid == "s1":
			return {"code": 0, "zpData": {}}
		raise AccountRiskError("BOSS 直聘风控拦截 (code 36)", is_cdp=True)

	mock_client.greet.side_effect = greet_side_effect
	mock_client.is_success.return_value = True
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "test", "--count", "3"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["recoverable"] is False
	assert "停止自动化访问" in parsed["error"]["recovery_action"]
	assert parsed["error"]["details"]["total_greeted"] == 1
	assert parsed["error"]["details"]["stopped_reason"] == "ACCOUNT_RISK"
	assert parsed["hints"]["next_actions"]
	# s2 只调用一次（不重试），s3 从未被调用
	assert [call.args[0] for call in mock_client.greet.call_args_list] == ["s1", "s2"]
	mock_cache.record_greet.assert_called_once_with("s1", "job_s1", account="")


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_budget_stop_does_not_retry_or_continue(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args):
	"""预算/时限停止是停止信号：不 sleep、不重试，也不继续下一个候选人。"""
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _three_jobs_client(mock_client_cls)

	def greet_side_effect(sid, jid, msg=""):
		if sid == "s1":
			return {"code": 0, "zpData": {}}
		raise RequestBudgetExceeded("fixture budget stop", code="REQUEST_BUDGET_EXHAUSTED")

	mock_client.greet.side_effect = greet_side_effect
	mock_client.is_success.return_value = True
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "test", "--count", "3"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"]
	assert parsed["error"]["details"]["total_greeted"] == 1
	assert parsed["error"]["details"]["stopped_reason"] == "REQUEST_BUDGET_EXHAUSTED"
	assert [call.args[0] for call in mock_client.greet.call_args_list] == ["s1", "s2"]
	# 失败候选人后面不再 sleep：只有 s1 成功后的那一次节流等待。
	mock_time.sleep.assert_called_once()


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_environment_risk_response_is_not_rate_limited(mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args):
	"""响应字典形态的 code 37 环境风控：文案含「频率」也不得被判成 RATE_LIMITED 的 ok:true 停批。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _three_jobs_client(mock_client_cls)

	risk_resp = {"code": 37, "message": "环境存在异常，请降低访问频率"}
	mock_client.greet.side_effect = lambda sid, jid, msg="": {"code": 0, "zpData": {}} if sid == "s1" else risk_resp
	mock_client.is_success.side_effect = lambda resp: resp.get("code", 0) == 0
	mock_client.parse_error.return_value = ("ENVIRONMENT_RISK", "环境存在异常，请降低访问频率")
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "test", "--count", "3"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "ENVIRONMENT_RISK"
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["details"]["stopped_reason"] == "ENVIRONMENT_RISK"
	assert parsed["error"]["details"]["total_greeted"] == 1
	assert [call.args[0] for call in mock_client.greet.call_args_list] == ["s1", "s2"]


# ── 本地记账失败：写操作已生效，不得提示重试 ─────────────


@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_greet_cache_write_failure_is_not_retryable(mock_cache_cls, mock_auth_cls, mock_get_platform, legacy_args):
	"""平台已确认招呼发出后记账失败：信封必须不可重试，且说明动作已生效。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.record_greet.side_effect = OSError("disk full")
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.greet.return_value = {"code": 0, "zpData": {}}
	mock_platform.is_success.return_value = True

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "greet", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == ERROR_CODES["LOCAL_STATE_WRITE_FAILED"]["recovery_action"]
	assert "不要自动重发" in parsed["error"]["message"]
	assert parsed["error"]["details"]["greeted"] is True
	assert parsed["error"]["details"]["recorded"] is False


@patch("boss_agent_cli.commands.greet.time")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
@patch("boss_agent_cli.commands.greet.CacheStore")
def test_batch_greet_cache_write_failure_stops_without_resending(
	mock_cache_cls, mock_auth_cls, mock_client_cls, mock_time, legacy_args
):
	"""记账失败不能落进通用重试分支：不重发已成功的招呼，也不继续下一个候选人。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.record_greet.side_effect = OSError("disk full")
	mock_client = _three_jobs_client(mock_client_cls)
	mock_client.greet.return_value = {"code": 0, "zpData": {}}
	mock_client.is_success.return_value = True
	mock_time.sleep = MagicMock()

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "batch-greet", "test", "--count", "3"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert parsed["error"]["recoverable"] is False
	details = parsed["error"]["details"]
	assert details["total_greeted"] == 1
	assert details["stopped_reason"] == "LOCAL_STATE_WRITE_FAILED"
	assert details["greeted"][0]["recorded"] is False
	# 只发出第一个招呼：不重发、不继续；记账失败后也不再节流等待。
	assert [call.args[0] for call in mock_client.greet.call_args_list] == ["s1"]
	mock_time.sleep.assert_not_called()


def _invoke_batch_with_real_platform(tmp_path, greet_response):
	"""走真实 ``BossPlatform`` 的批量招呼：只有最深层的 client 是假的。

	mock 平台层（``is_success`` / ``parse_error`` 一并 mock）测不出错误码
	在平台层与命令层之间的真实映射，结果未知的重放语义正是丢在这个接缝上。
	"""
	from boss_agent_cli.platforms.zhipin import BossPlatform

	raw_job = _make_raw_job("A", "s1")

	class _FakeClient:
		def __init__(self) -> None:
			self.greet_calls = 0

		def search_jobs(self, *a, **k):
			return {"code": 0, "zpData": {"jobList": [raw_job]}}

		def greet(self, sid, jid, msg=""):
			self.greet_calls += 1
			return greet_response

	client = _FakeClient()
	platform = BossPlatform(client)
	with (
		patch("boss_agent_cli.commands.greet.get_platform_instance", return_value=platform),
		patch("boss_agent_cli.commands.greet.AuthManager"),
		patch("boss_agent_cli.commands.greet.CacheStore") as mock_cache_cls,
		patch("boss_agent_cli.commands.greet.time") as mock_time,
	):
		mock_cache = _ctx_mock(mock_cache_cls)
		mock_cache.is_greeted.return_value = False
		mock_time.sleep = MagicMock()
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "batch-greet", "x", "--count", "1"]
		)
	return result, client


def test_batch_greet_unconfirmed_result_is_never_retried(tmp_path):
	"""结果未知的招呼不得自动重发，且信封必须带 replay_safe=false（Issue #984）。

	``ACTION_UNCONFIRMED`` 的语义是请求已发出、结果未知（平台层在
	``__cli_error_details__`` 里明确 ``replay_safe: false``）。批量路径原先把它
	当普通失败 sleep 后重发一次，最终还回 ``ok:true``：既可能重复打招呼，也让
	Agent 以为只是一次普通失败。
	"""
	unknown = {
		"code": -1,
		"message": "请求期间页面或响应中断；操作可能已生效，请先核对结果，勿直接重试",
		"zpData": {},
		"__cli_error_code__": "ACTION_UNCONFIRMED",
		"__cli_error_details__": {"outcome": "unknown", "replay_safe": False},
	}
	result, client = _invoke_batch_with_real_platform(tmp_path, unknown)

	assert result.exit_code == 1
	body = json.loads(result.stdout)
	assert body["ok"] is False
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["recoverable"] is False
	# 一次未知就地停批：重试等于在不知道是否送达时再发一次。
	assert client.greet_calls == 1
	details = body["error"]["details"]
	assert details["outcome_unknown"] is True
	assert details["replay_safe"] is False
	assert details["unconfirmed"]["details"]["replay_safe"] is False
	assert details["unconfirmed"]["security_id"] == "s1"
	# 已成功的部分仍随批次带回（这里是 0 条），Agent 不会对整批重来。
	assert details["total_greeted"] == 0
	assert "不要自动重发" in " ".join(body["hints"]["operator_actions"])


def test_batch_greet_unparsed_write_response_is_never_retried(tmp_path):
	"""浏览器通道的非 JSON 写响应同样不得自动重发（Issue #990）。

	传输层此前只返回裸 ``code: -1``，平台层判 ``UNKNOWN``，批量循环把它当普通
	失败 sleep 后重发同一个招呼，两次都失败还回 ``ok:true``；下一次运行会第三
	次发送。现在传输层把它标成 ``ACTION_UNCONFIRMED``，批量路径就地停批。
	"""
	unparsed = {
		"code": -1,
		"message": "browser response is not JSON",
		"zpData": {},
		"__cli_error_code__": "ACTION_UNCONFIRMED",
		"__cli_error_details__": {"outcome": "unknown", "replay_safe": False, "reason": "unparsed_response"},
	}
	result, client = _invoke_batch_with_real_platform(tmp_path, unparsed)

	assert result.exit_code == 1
	body = json.loads(result.stdout)
	assert body["ok"] is False
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["recoverable"] is False
	assert client.greet_calls == 1
	assert body["error"]["details"]["replay_safe"] is False
	assert body["error"]["details"]["outcome_unknown"] is True
	chat_cmd = body["hints"]["next_actions"][0]
	assert chat_cmd in body["error"]["recovery_action"]
	assert "hr chatmsg" not in body["error"]["recovery_action"]


def test_batch_greet_unknown_response_stops_without_resending(tmp_path):
	"""平台层仍判 ``UNKNOWN``（没有 CLI 内部码）时也不能 sleep 后重发。"""
	unknown = {"code": -1, "message": "browser response is not JSON", "zpData": {}}
	result, client = _invoke_batch_with_real_platform(tmp_path, unknown)

	assert result.exit_code == 1
	body = json.loads(result.stdout)
	assert body["ok"] is False
	assert body["error"]["code"] == "UNKNOWN"
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["replay_safe"] is False
	assert body["error"]["details"]["outcome_unknown"] is True
	chat_cmd = body["hints"]["next_actions"][0]
	assert chat_cmd in body["error"]["recovery_action"]
	assert "hr chatmsg" not in body["error"]["recovery_action"]
	# 一次未知就地停批：sleep 后重发就是重复写操作。
	assert client.greet_calls == 1
