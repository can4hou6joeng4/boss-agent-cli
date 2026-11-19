"""信封出口的脱敏、details 边界与退出码契约（Issue #2049–#2053）。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from boss_agent_cli import display
from boss_agent_cli.output import (
	envelope_error,
	emit_success,
	redact_sensitive,
	set_exit_codes_mode,
)


class _Ctx:
	obj: dict[str, Any] = {"json_output": False}


@pytest.fixture(autouse=True)
def _envelope_mode():
	set_exit_codes_mode("envelope")
	yield
	set_exit_codes_mode("envelope")


# ── Issue #2049：错误码元数据豁免只对 schema 的 error_codes 生效 ──────


def test_metadata_shape_does_not_bypass_sensitive_key_names() -> None:
	result = redact_sensitive(
		{"api_key": {"message": "sk-live-RAW", "recoverable": True, "recovery_action": "r"}}
	)

	assert result["api_key"] == "[REDACTED]"


def test_error_codes_metadata_stays_public() -> None:
	payload = {
		"error_codes": {
			"SESSION_TOKEN": {"message": "重新登录", "recoverable": True, "recovery_action": "boss login"}
		}
	}

	assert redact_sensitive(payload) == payload


# ── Issue #2050：recovery_action 与 message 同一脱敏契约 ──────────────


def test_recovery_action_redacts_embedded_credentials() -> None:
	env = json.loads(
		envelope_error(
			"x",
			code="INVALID_PARAM",
			message="访问 https://user:pass@host/p",
			recovery_action="访问 https://user:pass@host/p 后重试",
		)
	)

	assert "[REDACTED]" in env["error"]["recovery_action"]
	assert "user:pass" not in env["error"]["recovery_action"]


# ── Issue #2052：非映射 details 不能穿出 stdout 边界 ──────────────────


def test_non_mapping_details_keeps_seven_key_envelope() -> None:
	raw = envelope_error("x", code="UNKNOWN", message="m", details=["a", "b"])  # type: ignore[arg-type]
	env = json.loads(raw)

	assert set(env) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert env["error"]["code"] == "INTERNAL_ERROR"


# ── Issue #2051：TTY 分支遵守 --exit-codes sysexits ──────────────────


def test_tty_error_exit_code_follows_sysexits(monkeypatch) -> None:
	set_exit_codes_mode("sysexits")
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)

	with pytest.raises(SystemExit) as caught:
		display.handle_error_output(_Ctx(), "search", code="AUTH_REQUIRED", message="m")

	assert caught.value.code == 77


def test_tty_error_exit_code_defaults_to_one(monkeypatch) -> None:
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)

	with pytest.raises(SystemExit) as caught:
		display.handle_error_output(_Ctx(), "search", code="AUTH_REQUIRED", message="m")

	assert caught.value.code == 1


# ── Issue #2053：成功出口序列化兜底时设置失败退出码 ───────────────────


def test_emit_success_falls_back_to_failure_exit_code(capsys) -> None:
	with pytest.raises(SystemExit) as caught:
		emit_success("x", {float("nan"): 1})

	assert caught.value.code == 1
	assert json.loads(capsys.readouterr().out)["ok"] is False


def test_emit_success_fallback_uses_sysexits_internal_error(capsys) -> None:
	set_exit_codes_mode("sysexits")

	with pytest.raises(SystemExit) as caught:
		emit_success("x", {float("nan"): 1})

	assert caught.value.code == 70
	assert json.loads(capsys.readouterr().out)["error"]["code"] == "INTERNAL_ERROR"
