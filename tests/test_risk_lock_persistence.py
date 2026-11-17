"""风险锁写盘失败时，信封必须说明它只在当前进程生效（Issue #1270）。

主锁与 group 目录的锁都写失败时，同进程后续请求仍被内存中的锁拦住，但进程
退出后新进程读不到任何锁。`persisted: false` 不能只藏在 details 里：两条风控
出口（`handle_auth_errors` 与 `classify_login_error`）都要在 operator_actions
中明确「新进程不会再被阻止」。
"""

from unittest.mock import MagicMock, patch

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.display import handle_auth_errors, risk_lock_persistence_actions


def _risk_error(persisted: bool | None = None) -> PlatformRiskError:
	details: dict[str, object] = {"risk_lock": {"locked": True, "reason": "ACCOUNT_RISK", "platform_code": 36}}
	if persisted is not None:
		details["risk_lock"]["persisted"] = persisted  # type: ignore[index]
	return PlatformRiskError("ACCOUNT_RISK", details=details)


def _ctx(tmp_path) -> MagicMock:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin"}
	return ctx


def test_helper_only_fires_for_unpersisted_boolean_false():
	assert risk_lock_persistence_actions(None) == []
	assert risk_lock_persistence_actions({}) == []
	assert risk_lock_persistence_actions({"risk_lock": {"persisted": True}}) == []
	# 只认布尔 False：字符串 "false" 是状态漂移，不当成「未持久化」的证据。
	assert risk_lock_persistence_actions({"risk_lock": {"persisted": "false"}}) == []

	actions = risk_lock_persistence_actions({"risk_lock": {"persisted": False}})
	assert len(actions) == 1
	assert "新的 boss 进程" in actions[0]
	assert "写权限" in actions[0]


def test_auth_decorator_appends_persistence_action(tmp_path):
	ctx = _ctx(tmp_path)

	@handle_auth_errors("chat")
	def impl(ctx):
		raise _risk_error(False)

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "ACCOUNT_RISK"
	assert kwargs["recoverable"] is False
	assert kwargs["details"]["risk_lock"]["persisted"] is False
	# 原有风控指引保留，警示追加在 operator_actions 末尾。
	assert kwargs["hints"]["next_actions"]
	assert any("新的 boss 进程" in action for action in kwargs["hints"]["operator_actions"])


def test_auth_decorator_keeps_hints_when_lock_is_persisted(tmp_path):
	ctx = _ctx(tmp_path)

	@handle_auth_errors("chat")
	def impl(ctx):
		raise _risk_error(True)

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["hints"]["next_actions"]
	# 风控说明本身在 operator_actions 中（Issue #1522）；未持久化警示不应混入。
	operator_actions = kwargs["hints"]["operator_actions"]
	assert operator_actions
	assert not any("新的 boss 进程" in action for action in operator_actions)


def test_login_classifier_appends_persistence_action(tmp_path):
	payload = classify_login_error(_risk_error(False), _ctx(tmp_path))

	assert payload["code"] == "ACCOUNT_RISK"
	assert payload["recoverable"] is False
	assert payload["details"]["risk_lock"]["persisted"] is False
	assert any("新的 boss 进程" in action for action in payload["hints"]["operator_actions"])


def test_login_classifier_without_persistence_flag_keeps_plain_contract(tmp_path):
	payload = classify_login_error(_risk_error(), _ctx(tmp_path))

	assert payload["code"] == "ACCOUNT_RISK"
	assert payload["hints"]["next_actions"]
	# 风控说明本身在 operator_actions 中；未持久化警示不应混入。
	operator_actions = payload["hints"]["operator_actions"]
	assert operator_actions
	assert not any("新的 boss 进程" in action for action in operator_actions)
