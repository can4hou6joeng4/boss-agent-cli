"""信封脱敏、结果未知标记与只读归一的边界（Issue #1897 #1898 #1899 #1900）。"""

from __future__ import annotations

import io
import json
import types
import contextlib

import pytest

import boss_agent_cli.display as display
from boss_agent_cli.output import envelope_error, envelope_success, redact_sensitive_text

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


def _envelope(text: str) -> dict:
	payload = json.loads(text)
	assert set(payload) == _ENVELOPE_KEYS
	return payload


# ── Issue #1897：超大整数键不再让信封丢失 ───────────────────────────


def test_超大整数键在成功信封里走序列化兜底() -> None:
	payload = _envelope(envelope_success("x", {10**4300: "v"}))

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INTERNAL_ERROR"


def test_超大整数键在错误详情里走序列化兜底() -> None:
	payload = _envelope(envelope_error("x", code="RATE_LIMITED", message="m", details={10**4300: "v"}))

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INTERNAL_ERROR"


def test_正常信封不受影响() -> None:
	payload = _envelope(envelope_success("x", {"title": "后端"}))

	assert payload["ok"] is True
	assert payload["data"] == {"title": "后端"}


# ── Issue #1898：outcome_unknown 非布尔 fail-closed ──────────────────


@pytest.mark.parametrize("marker", [0, None, "", [], {}, "false", 1.0])
def test_非布尔_outcome_unknown_不允许重放(marker: object) -> None:
	payload = _envelope(
		envelope_error("batch-greet", code="RATE_LIMITED", message="m", details={"outcome_unknown": marker})
	)

	assert payload["error"]["details"]["replay_safe"] is False


@pytest.mark.parametrize("marker, expected", [(True, False), (False, True)])
def test_布尔_outcome_unknown_保持既有语义(marker: bool, expected: bool) -> None:
	payload = _envelope(
		envelope_error("batch-greet", code="RATE_LIMITED", message="m", details={"outcome_unknown": marker})
	)

	assert payload["error"]["details"]["replay_safe"] is expected


# ── Issue #1899：带引号形态与多段 Cookie 的脱敏 ──────────────────────


@pytest.mark.parametrize(
	"text",
	[
		'{"token": "abc123"}',
		"{'access_token': 'eyJhbGciOi'}",
		'{"password": "hunter2"}',
	],
)
def test_带引号的_json_形态被脱敏(text: str) -> None:
	redacted = redact_sensitive_text(text)

	assert "abc123" not in redacted
	assert "eyJhbGciOi" not in redacted
	assert "hunter2" not in redacted
	assert "[REDACTED]" in redacted


def test_多段_cookie_头整行脱敏() -> None:
	redacted = redact_sensitive_text("Cookie: SESSION=abc; wt2=xyz")

	assert "abc" not in redacted
	assert "xyz" not in redacted
	assert redacted == "Cookie: [REDACTED]"


def test_普通文本不被误伤() -> None:
	assert redact_sensitive_text("token_expires_in=3600") == "token_expires_in=3600"
	assert redact_sensitive_text("登录token=abc") == "登录token=[REDACTED]"


# ── Issue #1900：read_only_operation=True 参与 UNKNOWN 归一 ──────────


class _UnknownPlatform:
	def parse_error(self, response: object) -> tuple[str, str]:
		return "UNKNOWN", "平台返回了未识别的错误"


def _platform_error(*, read_only_operation: bool | None) -> dict:
	ctx = types.SimpleNamespace(obj={"json_output": True})
	buffer = io.StringIO()
	try:
		with contextlib.redirect_stdout(buffer):
			display.handle_platform_error_output(
				ctx,
				"some-write-cmd",
				_UnknownPlatform(),
				{"code": 999},
				fallback_message="fb",
				read_only_operation=read_only_operation,
			)
	except SystemExit:
		pass
	return _envelope(buffer.getvalue())["error"]


@pytest.mark.parametrize("read_only_operation", [None, False])
def test_写分支的_UNKNOWN_保持不可重试(read_only_operation: bool | None) -> None:
	error = _platform_error(read_only_operation=read_only_operation)

	assert error["code"] == "UNKNOWN"
	assert error["recoverable"] is False


def test_显式只读分支的_UNKNOWN_归一为可重试() -> None:
	error = _platform_error(read_only_operation=True)

	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
