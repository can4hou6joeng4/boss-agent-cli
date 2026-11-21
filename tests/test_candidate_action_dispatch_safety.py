"""候选人写请求的派发前失败判定（Issue #2296 / #2297 / #2298）。

greet / apply 每次只发一个浏览器 fetch。登录态、代理配置、首页导航与内核缺失
都在 fetch 之前失败，请求根本没有发出：预约必须释放，否则该职位会被 pending
永久挡住；无法证明未发出的异常则必须按结果未知输出，不能让 Agent 重试。
全部离线：平台、登录态与工作流控制都是 mock / 内存替身。
"""

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.browser_client import BrowserNavigationError
from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, resolve_policy
from boss_agent_cli.api.cdp_risk_lock import RiskLock
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.candidate_actions import (
	candidate_action_provably_unsent,
	run_candidate_action,
	was_provably_unsent,
)
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowControl


def _db(tmp_path) -> Any:
	return tmp_path / "cache" / "boss_agent.db"


def _claim_state(tmp_path, job_id: str, action: str) -> str | None:
	"""只读查询预约状态：``claim_candidate_action`` 会顺带插入 pending，不能用来断言。"""
	import sqlite3

	connection = sqlite3.connect(_db(tmp_path))
	try:
		row = connection.execute(
			"SELECT status FROM candidate_action_claims WHERE job_id = ? AND action = ?",
			(job_id, action),
		).fetchone()
	finally:
		connection.close()
	return str(row[0]) if row is not None else None


def _raise(exc: BaseException):
	def send():
		raise exc

	return send


def _risk_lock() -> RiskLock:
	return RiskLock(stoken_sha256=None, locked_at=1.0, reason="ACCOUNT_RISK", platform_code=32)


def test_predicate_recognizes_pre_dispatch_failures():
	"""派发前失败必须释放预约；真正未知的异常保持 pending。"""
	assert candidate_action_provably_unsent(AuthRequired("未登录")) is True
	assert candidate_action_provably_unsent(TokenRefreshFailed("刷新失败")) is True
	assert candidate_action_provably_unsent(ProxyConfigurationError("HTTPS_PROXY")) is True
	assert (
		candidate_action_provably_unsent(
			BrowserNavigationError("https://www.zhipin.com/", attempts=2, timeout_ms=15000, channel="cdp")
		)
		is True
	)
	assert candidate_action_provably_unsent(RuntimeError("Executable doesn't exist at /tmp/chromium")) is True
	assert candidate_action_provably_unsent(RequestBudgetExceeded("预算停止")) is True
	assert (
		candidate_action_provably_unsent(BrowserSourceUnavailable(resolve_policy("stored-cookie"), ("cdp",)))
		is True
	)
	assert candidate_action_provably_unsent(PlatformRiskError.from_lock(_risk_lock())) is True

	assert candidate_action_provably_unsent(TimeoutError("响应超时")) is False
	assert candidate_action_provably_unsent(ValueError("本地参数错误")) is False
	assert candidate_action_provably_unsent(PlatformRiskError("账号风险", details={"risk_lock": {}})) is False


def test_local_lock_error_marks_request_not_sent():
	"""本地风险锁在发请求前拦截，错误本身要带可核对的「未发送」证据。"""
	error = PlatformRiskError.from_lock(_risk_lock())

	assert error.details["request_sent"] is False
	assert error.details["risk_lock"]["reason"] == "ACCOUNT_RISK"


def test_run_candidate_action_releases_claim_on_local_risk_lock(tmp_path):
	with CacheStore(_db(tmp_path)) as cache:
		with pytest.raises(PlatformRiskError):
			run_candidate_action(
				cache,
				security_id="sec-1",
				job_id="job-1",
				action="greet",
				send=_raise(PlatformRiskError.from_lock(_risk_lock())),
			)
	assert _claim_state(tmp_path, "job-1", "greet") is None


# ── CLI ────────────────────────────────────────────────────────────


def _platform() -> MagicMock:
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.is_success.return_value = True
	platform.greet.return_value = {"code": 0, "zpData": {}}
	platform.apply.return_value = {"code": 0, "zpData": {}}
	return platform


@contextmanager
def _patched(module: str, platform: MagicMock):
	with (
		patch(f"boss_agent_cli.commands.{module}.AuthManager"),
		patch(f"boss_agent_cli.commands.{module}.get_platform_instance") as factory,
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		yield


def _invoke(module: str, platform: MagicMock, tmp_path, args: list[str]):
	with _patched(module, platform):
		return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_cli_greet_releases_claim_after_auth_required_then_retry_succeeds(tmp_path):
	"""未登录不在派发窗口内：第一次 AUTH_REQUIRED，登录后同一职位可以重新打招呼。"""
	platform = _platform()
	platform.greet.side_effect = AuthRequired("未登录，请先执行 boss --data-dir … login")

	first = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert first.exit_code == 1, first.output
	assert json.loads(first.stdout)["error"]["code"] == "AUTH_REQUIRED"
	assert _claim_state(tmp_path, "job-1", "greet") is None

	platform.greet.side_effect = None
	second = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert second.exit_code == 0, second.output
	assert json.loads(second.stdout)["data"]["message"] == "打招呼成功"
	assert _claim_state(tmp_path, "job-1", "greet") == "sent"


def test_cli_greet_reports_unconfirmed_for_unknown_dispatch(tmp_path):
	"""无法证明未发出的异常：首次调用就报结果未知，不能指引 Agent 去撞 pending 预约。"""
	platform = _platform()
	platform.greet.side_effect = TimeoutError("response timed out")

	first = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert first.exit_code == 1, first.output
	body = json.loads(first.stdout)
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["outcome_unknown"] is True
	assert body["error"]["details"]["replay_safe"] is False
	assert body["error"]["details"]["cause"] == "TimeoutError"
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"

	second = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert second.exit_code == 1
	assert json.loads(second.stdout)["error"]["code"] == "ACTION_UNCONFIRMED"
	assert platform.greet.call_count == 1


def test_cli_apply_releases_claim_after_navigation_failure(tmp_path):
	platform = _platform()
	platform.apply.side_effect = BrowserNavigationError(
		"https://www.zhipin.com/", attempts=2, timeout_ms=15000, channel="cdp"
	)

	first = _invoke("apply", platform, tmp_path, ["apply", "sec-1", "job-1"])

	assert first.exit_code == 1, first.output
	assert json.loads(first.stdout)["error"]["code"] == "BROWSER_NAV_TIMEOUT"
	assert _claim_state(tmp_path, "job-1", "apply") is None

	platform.apply.side_effect = None
	second = _invoke("apply", platform, tmp_path, ["apply", "sec-1", "job-1"])

	assert second.exit_code == 0, second.output


_RAW_JOB = {
	"encryptJobId": "job-1",
	"securityId": "sec-1",
	"jobName": "后端开发",
	"brandName": "TestCo",
	"salaryDesc": "20K",
	"cityName": "北京",
	"jobExperience": "3-5年",
	"jobDegree": "本科",
	"bossName": "张",
	"bossTitle": "HR",
}


def _batch_platform() -> MagicMock:
	platform = _platform()
	platform.name = "zhipin"
	platform.search_jobs.return_value = {"code": 0, "zpData": {"jobList": [dict(_RAW_JOB)]}}
	platform.unwrap_data.side_effect = lambda payload: payload.get("zpData", payload)
	return platform


def test_batch_greet_releases_claim_after_pre_dispatch_failure(tmp_path):
	"""批次内的派发前失败同样不占用预约（只读重试不会发出请求）。"""
	platform = _batch_platform()
	platform.greet.side_effect = ProxyConfigurationError("HTTPS_PROXY")

	with patch("boss_agent_cli.commands.greet.time.sleep"):
		result = _invoke("greet", platform, tmp_path, ["batch-greet", "golang", "--count", "1"])

	assert result.exit_code == 0, result.output
	assert _claim_state(tmp_path, "job-1", "greet") is None


def test_batch_greet_stops_on_network_error_response(tmp_path):
	"""5xx 响应不是可重试失败：不 sleep / 不重发，预约保持 pending（Issue #2316）。"""
	platform = _batch_platform()
	platform.greet.return_value = {"code": 500, "message": "server error"}
	platform.is_success.side_effect = lambda response: response.get("code") == 0
	platform.parse_error.return_value = ("NETWORK_ERROR", "server error")

	with patch("boss_agent_cli.commands.greet.time.sleep") as sleep:
		result = _invoke("greet", platform, tmp_path, ["batch-greet", "golang", "--count", "1"])

	assert result.exit_code == 1, result.output
	assert sleep.call_count == 0
	assert platform.greet.call_count == 1
	error = json.loads(result.stdout)["error"]
	assert error["recoverable"] is False
	assert error["details"]["outcome_unknown"] is True
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


# ── wizard ─────────────────────────────────────────────────────────


class _RecordingControl:
	def __init__(self) -> None:
		self.begun = 0
		self.cleared = 0

	def begin_write(self, recovery_action: str) -> None:
		self.begun += 1

	def clear_write(self) -> None:
		self.cleared += 1


class _FakeCandidatePlatform:
	def __init__(self, error: BaseException | None = None) -> None:
		self.error = error

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def greet(self, security_id, job_id, message=""):
		if self.error is not None:
			raise self.error
		return {"code": 0, "data": {"greeted": True}}

	def is_success(self, response) -> bool:
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "UNKNOWN", "boom"


def _action_context(tmp_path, platform, control: _RecordingControl) -> ActionContext:
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args: platform,
	)
	return context.with_workflow_control(control)  # type: ignore[arg-type]


def test_wizard_clears_write_marker_when_dispatch_never_started(tmp_path):
	control = _RecordingControl()
	platform = _FakeCandidatePlatform(ProxyConfigurationError("HTTPS_PROXY"))
	context = _action_context(tmp_path, platform, control)

	with pytest.raises(ProxyConfigurationError):
		DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})

	assert control.begun == 1
	assert control.cleared == 1
	assert _claim_state(tmp_path, "job-1", "greet") is None


def test_wizard_keeps_write_marker_when_dispatch_is_unknown(tmp_path):
	control = _RecordingControl()
	platform = _FakeCandidatePlatform(TimeoutError("response timed out"))
	context = _action_context(tmp_path, platform, control)

	with pytest.raises(TimeoutError):
		DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})

	assert control.begun == 1
	assert control.cleared == 0
	assert _claim_state(tmp_path, "job-1", "greet") == "pending"


def test_workflow_control_clear_write_drops_persisted_marker():
	store = MagicMock()
	control = WorkflowControl(run_id="run-1", step_name="candidate_greet", store=store)

	control.clear_write()

	store.set_step_error.assert_called_once_with("run-1", "candidate_greet", None)


def test_workflow_control_clear_write_is_fail_closed_on_local_failure():
	"""清理失败按 fail-closed 处理：标记仍在，恢复时仍拒绝重放，不替换原始异常。"""
	store = MagicMock()
	store.set_step_error.side_effect = RuntimeError("cache busy")
	control = WorkflowControl(run_id="run-1", step_name="candidate_greet", store=store)

	control.clear_write()


def test_begin_write_failure_marks_request_unsent():
	"""写前标记落不了盘时写请求没有发出：replay_safe=False 与 request_sent 是两条语义。"""
	store = MagicMock()
	store.set_step_error.return_value = False
	control = WorkflowControl(run_id="run-1", step_name="candidate_greet", store=store)

	with pytest.raises(WorkflowActionError) as caught:
		control.begin_write("boss chat")

	error = caught.value
	assert error.code == "CACHE_RECORD_INVALID"
	# 重试同一条命令必然复现，仍不可自动重跑……
	assert error.replay_safe is False
	assert error.recoverable is False
	# ……但请求确实没发出，预约可以安全释放（Issue #2299）。
	assert error.details == {"request_sent": False}
	assert was_provably_unsent(error) is True


def test_wizard_begin_write_failure_releases_claim(tmp_path):
	"""begin_write 失败时平台方法没有被调用，pending 预约不能永久占用该职位。"""
	store = MagicMock()
	store.set_step_error.return_value = False
	control = WorkflowControl(run_id="run-1", step_name="candidate_greet", store=store)
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform, control)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})

	assert caught.value.code == "CACHE_RECORD_INVALID"
	assert _claim_state(tmp_path, "job-1", "greet") is None
	# 标记从未落盘，清理调用只是尽力撤销；store 返回 False 也不算失败。
	assert store.set_step_error.call_args_list[-1].args == ("run-1", "candidate_greet", None)
