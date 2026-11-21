"""审计批次六十二：登录错误分类的状态码上下文、风控优先与回显脱敏（Issue #2335–#2337）。

- #2335：401 只在明确状态码上下文里判为登录过期，端口 4010 / step 401 不再误判；
- #2336：风控文本判定先于 ValueError / timeout / CDP 等启发式，混合文案不再把
  硬边界降级成可重试错误；
- #2337：分类入口对底层异常文本统一脱敏，`wt2` / `at` 进入敏感键名单，
  wizard 预检渲染前同样脱敏。

全部离线：纯函数与 mock 渲染，不发平台请求。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import click
import pytest

from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.output import redact_sensitive_text


def _classify(exc: Exception) -> dict:
	ctx = click.Context(click.Command("login"), obj={"platform": "zhipin", "data_dir": Path("/tmp/fake")})
	return classify_login_error(exc, ctx)


# ── #2335：401 只在明确状态码上下文命中 ──────────────────────────


@pytest.mark.parametrize(
	"text",
	[
		"connect to 127.0.0.1:4010 failed",
		"failed at step 401",
		"retry after 401 ms",
	],
)
def test_bare_401_number_is_not_login_expired(text):
	out = _classify(RuntimeError(text))

	assert out["code"] != "LOGIN_EXPIRED"


@pytest.mark.parametrize(
	"text",
	[
		"401",
		"HTTP 401",
		"status: 401",
		"request failed with 401",
		"401 Unauthorized",
	],
)
def test_explicit_401_status_is_login_expired(text):
	out = _classify(RuntimeError(text))

	assert out["code"] == "LOGIN_EXPIRED"


# ── #2336：风控判定先于文本启发式 ────────────────────────────────

_SECURITY_URL = "https://www.zhipin.com/web/passport/zp/security.html"


def test_timeout_with_security_page_stays_risk_control():
	out = _classify(RuntimeError(f"Timeout 30000ms exceeded while waiting for {_SECURITY_URL}"))

	assert out["code"] == "LOGIN_RISK_CONTROL"
	assert out["recoverable"] is False


def test_value_error_with_risk_text_stays_risk_control():
	out = _classify(ValueError(f"navigate to {_SECURITY_URL} failed"))

	assert out["code"] == "LOGIN_RISK_CONTROL"
	assert out["recoverable"] is False


def test_plain_timeout_is_still_login_timeout():
	out = _classify(RuntimeError("Timeout 30000ms exceeded while waiting for selector"))

	assert out["code"] == "LOGIN_TIMEOUT"


def test_plain_value_error_is_still_invalid_param():
	out = _classify(ValueError("--cookie-source 与 --curl-file 不能同时使用"))

	assert out["code"] == "INVALID_PARAM"
	assert out["recoverable"] is False


# ── #2337：分类回显与预检渲染统一脱敏 ────────────────────────────


def test_classify_redacts_boss_session_cookie_names():
	for text, secret in (
		("cookie read failed: wt2=SECRET-CANDIDATE", "SECRET-CANDIDATE"),
		("auth failed at=SECRET-ACCESS", "SECRET-ACCESS"),
	):
		out = _classify(RuntimeError(text))
		assert secret not in str(out["message"])
		assert "[REDACTED]" in str(out["message"])


def test_redaction_does_not_touch_substring_keys():
	text = "cat=whiskers format=json at=SECRET-ACCESS"

	redacted = redact_sensitive_text(text)

	assert "cat=whiskers" in redacted
	assert "format=json" in redacted
	assert "SECRET-ACCESS" not in redacted


def test_wizard_preflight_renders_redacted_message(monkeypatch):
	from boss_agent_cli.wizard import preflight

	printed: list[str] = []
	console = MagicMock()
	console.print.side_effect = lambda renderable, **_: printed.append(
		str(getattr(renderable, "renderable", renderable)),
	)
	monkeypatch.setattr(preflight.display, "console", console)
	ctx = click.Context(click.Command("wizard"), obj={"platform": "zhipin", "data_dir": Path("/tmp/fake")})

	preflight._render_login_failure(ctx, RuntimeError("cookie read failed: wt2=SECRET-CANDIDATE"))

	joined = "\n".join(printed)
	assert "SECRET-CANDIDATE" not in joined
	assert "[REDACTED]" in joined
