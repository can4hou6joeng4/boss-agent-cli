"""Issue #2898 / #2899：复合聊天动作的写前证据与 resume --friend-id 提示。

`hr request-resume` 曾经把写请求确定未发出的异常一律报成 `ACTION_UNCONFIRMED`。
复合动作不能按异常类型推断写没写，因此由客户端在写入边界标注
``request_sent=False``，命令层与向导只消费这条显式证据；写后失败仍按结果未知
保护。另覆盖 `hr resume --friend-id` 漏 `--exchange` 时的恢复指引。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api._base_client import RequestTextEncodingError
from boss_agent_cli.api.browser_client import (
	BrowserNavigationError,
	ChatTabCdpUnreachable,
	RecruiterChatTabRequired,
)
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient, _mark_request_not_sent
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.candidate_actions import (
	exception_reports_request_unsent,
	was_provably_unsent,
)
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

REQUEST_RESUME_MODULE = "boss_agent_cli.commands.recruiter.request_resume"
RESUME_MODULE = "boss_agent_cli.commands.recruiter.resume"
FRIEND_ID = 123


def _request_resume(tmp_path: Path, exc: BaseException) -> tuple[int, dict]:
	with (
		patch(REQUEST_RESUME_MODULE + ".AuthManager"),
		patch(REQUEST_RESUME_MODULE + ".get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value.exchange_request_by_friend.side_effect = exc
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "request-resume", str(FRIEND_ID)]
		)
	return result.exit_code, json.loads(result.output)


def _client() -> BossRecruiterClient:
	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "stoken": "synthetic", "user_agent": "TestAgent"}
	return BossRecruiterClient(auth)


# ── Issue #2898：命令层消费显式写前证据 ────────────────────────────


@pytest.mark.parametrize(
	"make_error, expected_code, expected_recoverable",
	[
		(lambda: ProxyConfigurationError("HTTPS_PROXY"), "INVALID_PARAM", False),
		(
			lambda: BrowserNavigationError(
				"https://www.zhipin.com/", attempts=2, timeout_ms=1000, channel="cdp"
			),
			"BROWSER_NAV_TIMEOUT",
			True,
		),
		(lambda: RequestTextEncodingError("bad"), "INVALID_PARAM", False),
		(lambda: ChatTabCdpUnreachable("cdp down"), "CDP_UNAVAILABLE", True),
		(lambda: RecruiterChatTabRequired(), "RECRUITER_CHAT_TAB_REQUIRED", True),
	],
)
def test_request_resume_predispatch_failure_uses_specific_contract(
	tmp_path: Path, make_error, expected_code: str, expected_recoverable: bool
) -> None:
	exc = make_error()
	_mark_request_not_sent(exc)

	exit_code, payload = _request_resume(tmp_path, exc)

	assert exit_code == 1
	assert payload["ok"] is False
	error = payload["error"]
	assert error["code"] == expected_code
	assert error["code"] != "ACTION_UNCONFIRMED"
	assert error["recoverable"] is expected_recoverable


def test_request_resume_unmarked_failure_stays_unconfirmed(tmp_path: Path) -> None:
	"""没有显式证据时不能按异常类型猜测：写后同类型异常必须保持结果未知。"""
	exc = ProxyConfigurationError("HTTPS_PROXY")

	exit_code, payload = _request_resume(tmp_path, exc)

	assert exit_code == 1
	error = payload["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["recoverable"] is False
	assert error["details"]["requested"] is None
	assert was_provably_unsent(exc) is False


def test_exchange_missing_write_snapshot_marks_request_unsent() -> None:
	"""写前 friend_detail / 聊天快照失败发生在聊天页脚本之前，必须带证据。"""
	client = _client()
	try:
		friend_error = ProxyConfigurationError("HTTPS_PROXY")
		with patch.object(client, "_require_chat_friend_data", side_effect=friend_error):
			with pytest.raises(ProxyConfigurationError) as caught:
				client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
		assert caught.value is friend_error
		assert exception_reports_request_unsent(friend_error)

		snapshot_error = RequestTextEncodingError("bad")
		friend = {"friendId": FRIEND_ID, "uid": FRIEND_ID, "securityId": "sid"}
		with (
			patch.object(client, "_require_chat_friend_data", return_value=friend),
			patch.object(client, "_chat_history_snapshot", side_effect=snapshot_error),
		):
			with pytest.raises(RequestTextEncodingError) as caught:
				client.exchange_request_by_friend(FRIEND_ID, exchange_type=4)
		assert caught.value is snapshot_error
		assert exception_reports_request_unsent(snapshot_error)
	finally:
		client.close()


@pytest.mark.parametrize(
	"make_error",
	[
		lambda: ChatTabCdpUnreachable("cdp down"),
		lambda: RecruiterChatTabRequired(),
	],
	ids=["chat-tab-cdp-unreachable", "recruiter-chat-tab-required"],
)
def test_chat_frontend_predispatch_failure_marks_request_unsent(make_error) -> None:
	"""聊天页脚本发送前的失败由共用执行器标注（Issue #1455 的语义落成证据）。"""
	client = _client()
	exc = make_error()
	browser = MagicMock()
	browser.evaluate_js.side_effect = exc
	try:
		with patch.object(client, "_get_browser", return_value=browser):
			with pytest.raises(type(exc)) as caught:
				client._run_chat_frontend_action_unpaced(
					friend_data={"friendId": FRIEND_ID},
					action_js="",
					require_security_id=False,
					settle_ms=0,
				)
		assert caught.value is exc
		assert exception_reports_request_unsent(exc)
	finally:
		client.close()


def test_browser_lookup_failure_marks_request_unsent() -> None:
	client = _client()
	exc = BrowserNavigationError("https://www.zhipin.com/", attempts=2, timeout_ms=1000, channel="cdp")
	try:
		with patch.object(client, "_get_browser", side_effect=exc):
			with pytest.raises(BrowserNavigationError) as caught:
				client._run_chat_frontend_action_unpaced(
					friend_data={"friendId": FRIEND_ID},
					action_js="",
					require_security_id=False,
					settle_ms=0,
				)
		assert caught.value is exc
		assert exception_reports_request_unsent(exc)
	finally:
		client.close()


@pytest.mark.parametrize(
	"exc",
	[AccountRiskError("risk"), RequestBudgetExceeded("budget")],
	ids=["account-risk", "budget-stop"],
)
def test_mark_helper_never_marks_risk_or_stop(exc: BaseException) -> None:
	"""风控 / 停止异常不得被标成可重试的「未发出」。"""
	_mark_request_not_sent(exc)
	assert exception_reports_request_unsent(exc) is False


# ── Issue #2898：向导写标记只按显式证据撤销 ────────────────────────


class _ExchangePlatform:
	def __init__(self, failures: list[BaseException]) -> None:
		self.failures = list(failures)
		self.calls = 0

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict:
		self.calls += 1
		if self.failures:
			raise self.failures.pop(0)
		return {"code": 0, "zpData": {"requested": True}}

	def is_success(self, response: dict) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict) -> dict:
		return response.get("zpData") or {}

	def __enter__(self) -> "_ExchangePlatform":
		return self

	def __exit__(self, *args: object) -> bool:
		return False


def _plan() -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline",
		inputs={"friend_id": FRIEND_ID},
		requested_steps=("recruiter_request_resume",),
		mode="headless",
	)


def _context(tmp_path: Path, platform: _ExchangePlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		recruiter_factory=lambda *args, **kwargs: platform,
	)


@pytest.mark.parametrize(
	"make_error, expected_code",
	[
		(lambda: ProxyConfigurationError("HTTPS_PROXY"), "INVALID_PARAM"),
		(
			lambda: BrowserNavigationError(
				"https://www.zhipin.com/", attempts=2, timeout_ms=1000, channel="cdp"
			),
			"BROWSER_NAV_TIMEOUT",
		),
		(lambda: RequestTextEncodingError("bad"), "INVALID_PARAM"),
		(lambda: ChatTabCdpUnreachable("cdp down"), "CDP_UNAVAILABLE"),
		(lambda: RecruiterChatTabRequired(), "RECRUITER_CHAT_TAB_REQUIRED"),
	],
)
def test_wizard_clears_write_marker_for_marked_predispatch_failure(
	tmp_path: Path, make_error, expected_code: str
) -> None:
	exc = make_error()
	_mark_request_not_sent(exc)
	platform = _ExchangePlatform([exc])
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="run-unsent", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == expected_code
		assert first["error"].get("replay_safe") is not False
		second = runner.run(_plan(), context, run_id="run-unsent", max_retries=0)

	assert second["status"] == "completed"
	assert platform.calls == 2


def test_wizard_keeps_write_marker_without_explicit_evidence(tmp_path: Path) -> None:
	exc = ProxyConfigurationError("HTTPS_PROXY")
	platform = _ExchangePlatform([exc])
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="run-unknown", max_retries=0)
		assert first["status"] == "failed"
		# 写前标记仍在：错误码随分类记录，但 replay_safe=false 与核对指引
		# 保证 --resume 不会重放可能已生效的写操作。
		assert first["error"]["replay_safe"] is False
		assert "写请求在途" in first["error"]["message"]
		assert "hr chatmsg 123" in first["error"]["recovery_action"]
		second = runner.run(_plan(), context, run_id="run-unknown", max_retries=0)

	assert second["status"] == "failed"
	assert second["error"]["replay_safe"] is False
	assert platform.calls == 1


# ── Issue #2899：hr resume --friend-id 漏 --exchange 的提示 ─────────


def test_resume_friend_id_without_exchange_points_at_the_flag(tmp_path: Path) -> None:
	with (
		patch(RESUME_MODULE + ".AuthManager"),
		patch(RESUME_MODULE + ".get_recruiter_platform_instance"),
	):
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "hr", "resume", "--friend-id", str(FRIEND_ID)],
		)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--exchange" in error["message"]
	assert "hr resume --friend-id 123 --exchange" in error["recovery_action"]
	next_actions = payload["hints"]["next_actions"]
	assert any("hr resume --friend-id 123 --exchange" in item for item in next_actions)


def test_resume_without_friend_id_keeps_existing_view_hint(tmp_path: Path) -> None:
	with (
		patch(RESUME_MODULE + ".AuthManager"),
		patch(RESUME_MODULE + ".get_recruiter_platform_instance"),
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "hr", "resume"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--exchange" not in error["message"]
	assert "hr applications" in " ".join(payload["hints"]["next_actions"])


def test_resume_exchange_without_friend_id_keeps_dedicated_hint(tmp_path: Path) -> None:
	with (
		patch(RESUME_MODULE + ".AuthManager"),
		patch(RESUME_MODULE + ".get_recruiter_platform_instance"),
	):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "resume", "--exchange"]
		)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	error = payload["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "--friend-id" in error["message"]
	assert "hr chat" in " ".join(payload["hints"]["next_actions"])
