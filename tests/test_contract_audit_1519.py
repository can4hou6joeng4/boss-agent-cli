"""错误码契约审计（Issue #1519–#1524）。

覆盖：只读命令的 UNKNOWN 归一、agent 熔断错误码、wizard 联系人缺失不可重试、
风控 hints 的通道划分、MCP 只读白名单、agent 恢复命令的角色保留。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_platform_error_output,
	platform_error_contract,
	read_only_platform_code,
	registry_recovery_for_ctx,
	risk_error_contract,
)
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_server import _READ_ONLY_TOOL_NAMES, _tool_may_write
from boss_agent_cli.wizard.actions import _candidate_current_security_id
from boss_agent_cli.wizard.runner import WorkflowActionError


def _ctx(tmp_path: Path) -> MagicMock:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin", "role": "recruiter"}
	return ctx


class _UnknownPlatform:
	def parse_error(self, response):
		return "UNKNOWN", "平台返回了未识别的错误"


def test_read_only_unknown_is_mapped_to_network_error(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		handle_platform_error_output(
			ctx, "chat", _UnknownPlatform(), {"code": 12345}, fallback_message="沟通列表获取失败"
		)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "NETWORK_ERROR"
	assert kwargs["recoverable"] is True
	assert kwargs["recovery_action"] == "重试"


def test_write_unknown_keeps_nonreplayable_contract(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		handle_platform_error_output(
			ctx, "apply", _UnknownPlatform(), {"code": 12345}, fallback_message="投递失败"
		)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "UNKNOWN"
	assert kwargs["recoverable"] is False


def test_read_only_platform_code_is_conservative_for_unknown_commands() -> None:
	assert read_only_platform_code("search", "UNKNOWN") == "NETWORK_ERROR"
	assert read_only_platform_code("chat", "UNKNOWN") == "NETWORK_ERROR"
	assert read_only_platform_code("apply", "UNKNOWN") == "UNKNOWN"
	assert read_only_platform_code("some-future-command", "UNKNOWN") == "UNKNOWN"
	assert read_only_platform_code("search", "ACCOUNT_RISK") == "ACCOUNT_RISK"


def test_platform_error_contract_read_only_unknown_uses_network_recovery() -> None:
	recoverable, recovery_action, hints = platform_error_contract("UNKNOWN", read_only=True)
	assert recoverable is True
	assert recovery_action == "重试"
	assert hints is None


def test_search_unknown_returns_network_error_envelope(tmp_path: Path) -> None:
	from boss_agent_cli.search_filters import SearchPipelinePlatformError

	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.get_platform_instance"),
		patch("boss_agent_cli.commands.search.CacheStore") as mock_cache_cls,
		patch(
			"boss_agent_cli.commands.search.run_search_pipeline",
			side_effect=SearchPipelinePlatformError("UNKNOWN", "平台返回了未识别的错误"),
		),
	):
		cache = mock_cache_cls.return_value
		cache.__enter__ = lambda s: cache
		cache.__exit__ = lambda s, *a: None
		cache.get_search.return_value = None
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "Python"])

	payload = json.loads(result.stdout)
	assert result.exit_code == 1
	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True
	assert payload["error"].get("details", {}).get("replay_safe") is not False


def test_safety_stop_uses_circuit_breaker_code() -> None:
	state = {"autonomy": {"circuit_breaker": {"open": True, "reason": "manual-stop"}}}
	guard = SafetyGuard(AutomationConfig(), state, dry_run=False)

	with pytest.raises(RequestBudgetExceeded) as excinfo:
		guard.raise_if_stopped()

	assert excinfo.value.code == "CIRCUIT_BREAKER_OPEN"


def test_agent_circuit_breaker_envelope_uses_agent_recovery(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)

	@handle_auth_errors("agent run")
	def impl(ctx):
		raise RequestBudgetExceeded("熔断已打开", code="CIRCUIT_BREAKER_OPEN")

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "CIRCUIT_BREAKER_OPEN"
	assert kwargs["recoverable"] is True
	assert any("agent stats" in action for action in kwargs["hints"]["next_actions"])
	assert not any("冷却" in action for action in kwargs["hints"].get("operator_actions", []))


def test_wizard_missing_security_id_is_not_retryable() -> None:
	with pytest.raises(WorkflowActionError) as excinfo:
		_candidate_current_security_id({"uid": "u1"})

	error = excinfo.value
	assert error.code == "NETWORK_ERROR"
	assert error.recoverable is False
	assert "自动重试" in str(error.recovery_action)


def test_risk_contract_separates_commands_from_operator_guidance(tmp_path: Path) -> None:
	recovery, hints = risk_error_contract(
		"ENVIRONMENT_RISK_LOCKED",
		clean_command=f"boss --data-dir {tmp_path.resolve()} clean --risk-lock",
		doctor_command=f"boss --data-dir {tmp_path.resolve()} doctor",
	)

	assert hints["next_actions"] == [f"boss --data-dir {tmp_path.resolve()} doctor"]
	operator_actions = hints["operator_actions"]
	assert any("不要刷新 Token" in action for action in operator_actions)
	assert any("clean --risk-lock" in action for action in operator_actions)
	# 真人指引不再是 Agent 自动执行命令的通道。
	assert not any(action.startswith("不要") for action in hints["next_actions"])
	assert recovery


def test_risk_contract_context_rewrites_operator_actions(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	_, hints = risk_error_contract("ACCOUNT_RISK", ctx=ctx)
	doctor = f"boss --data-dir {tmp_path.resolve()} doctor"
	assert hints["next_actions"] == [doctor]
	assert any("不要通过 CDP" in action for action in hints["operator_actions"])


def test_mcp_read_only_whitelist_covers_pure_read_tools() -> None:
	for name in ("boss_agent_stats", "boss_agent_pending", "boss_agent_review", "boss_chat_summary"):
		assert name in _READ_ONLY_TOOL_NAMES
		assert _tool_may_write(name) is False


def test_agent_recovery_commands_keep_role(tmp_path: Path) -> None:
	ctx = _ctx(tmp_path)
	text = registry_recovery_for_ctx(ctx, "执行 boss agent stats 查看熔断，再 boss agent run --resume")
	prefix = f"boss --data-dir {tmp_path.resolve()} --role recruiter"
	assert f"{prefix} agent stats" in text
	assert f"{prefix} agent run --resume" in text

	# 非 agent 命令不注入角色，避免 doctor / clean 多出无关键。
	doctor_text = registry_recovery_for_ctx(ctx, "先跑 boss doctor 诊断")
	assert f"boss --data-dir {tmp_path.resolve()} doctor" in doctor_text
	assert "--role" not in doctor_text
