"""登录失败分类与 cURL 导入边界的离线回归（Issue #2307–#2313）。

覆盖：确定性异常不再兜成可重试网络错误、curl 分支保留限流 / 代理 / 本地 IO
契约、文本启发式的词边界、cURL 文件有界读取、BOM / 大小写兼容、本地 IO 文案、
风控 details 容器漂移与 Cookie 诊断脱敏。全部离线：AuthManager 与浏览器链路都是
mock，不发出平台请求。
"""

import json
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import PlatformRateLimitError, PlatformRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.auth.cookie_extract import _try_extract
from boss_agent_cli.auth.curl_import import parse_curl_auth
from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.main import cli

_CURL = "curl 'https://www.zhipin.com/wapi/test' -b 'wt2=test-session'"


def _classify(exc: Exception, tmp_path) -> dict:
	ctx = click.Context(click.Command("login"), obj={"platform": "zhipin", "data_dir": tmp_path})
	return classify_login_error(exc, ctx)


def _invoke(tmp_path, args, *, input_text=None):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", *args], input=input_text)


# ── #2307：确定性异常不再兜成 NETWORK_ERROR ────────────────────────


@pytest.mark.parametrize("exc_type", [TypeError, KeyError, AttributeError, IndexError], ids=["type", "key", "attr", "index"])
def test_login_internal_error_is_not_retryable(tmp_path, exc_type):
	mock_auth = MagicMock()
	mock_auth.login.side_effect = exc_type("internal-marker")
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, [])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "INTERNAL_ERROR"
	assert body["error"]["recoverable"] is False
	assert body["error"]["message"] == f"登录流程内部错误: {exc_type.__name__}"
	# 登录输入可能夹带凭据，不回显异常文本。
	assert "internal-marker" not in result.stdout


def test_login_local_io_error_is_deterministic(tmp_path):
	mock_auth = MagicMock()
	mock_auth.login.side_effect = PermissionError(13, "Permission denied", "/private/auth")
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, [])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "LOCAL_IO_ERROR"
	assert body["error"]["recoverable"] is False
	# 本地失败不一定发生在登录之后：文案不能断言「登录成功」。
	assert "登录成功" not in body["error"]["message"]
	assert "未保存" in body["error"]["message"]


# ── #2308：curl 分支保留专用错误契约 ──────────────────────────────


@pytest.mark.parametrize(
	("error", "code", "recoverable"),
	[
		(PlatformRateLimitError("登录态验证被平台限流"), "RATE_LIMITED", True),
		(ProxyConfigurationError("HTTPS_PROXY=http://:bad"), "INVALID_PARAM", False),
		(PermissionError(13, "Permission denied", "/private/auth"), "LOCAL_IO_ERROR", False),
	],
	ids=["rate-limit", "proxy", "local-io"],
)
def test_curl_import_keeps_typed_error_contract(tmp_path, error, code, recoverable):
	mock_auth = MagicMock()
	mock_auth.import_curl.side_effect = error
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, ["--curl-file", "-"], input_text=_CURL)

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == code
	assert body["error"]["recoverable"] is recoverable


def test_curl_import_proxy_error_points_at_proxy_env(tmp_path):
	mock_auth = MagicMock()
	mock_auth.import_curl.side_effect = ProxyConfigurationError("HTTPS_PROXY=http://:bad")
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, ["--curl-file", "-"], input_text=_CURL)

	body = json.loads(result.stdout)
	assert "HTTPS_PROXY" in body["error"]["recovery_action"]


# ── #2309：文本启发式的词边界与限流口径 ────────────────────────────


def test_port_starting_with_403_is_not_risk_control(tmp_path):
	payload = _classify(ConnectionError("connect ECONNREFUSED 127.0.0.1:4030"), tmp_path)

	assert payload["code"] == "CDP_UNAVAILABLE"
	assert "风控" not in str(payload["message"])


def test_port_starting_with_429_is_not_rate_limited(tmp_path):
	payload = _classify(ConnectionError("connect ECONNREFUSED 127.0.0.1:4290"), tmp_path)

	assert payload["code"] == "CDP_UNAVAILABLE"


def test_risk_substring_in_plain_word_does_not_trigger_risk(tmp_path):
	payload = _classify(RuntimeError("asterisk dial failed"), tmp_path)

	assert payload["code"] == "NETWORK_ERROR"


def test_timeout_substring_in_path_is_not_login_timeout(tmp_path):
	payload = _classify(RuntimeError("failed to open /tmp/timeout-file"), tmp_path)

	assert payload["code"] == "NETWORK_ERROR"


def test_timeout_text_still_classified(tmp_path):
	payload = _classify(RuntimeError("Timeout 15000ms exceeded"), tmp_path)

	assert payload["code"] == "LOGIN_TIMEOUT"


def test_security_page_text_is_risk_control(tmp_path):
	payload = _classify(RuntimeError("redirected to /web/passport/zp/security.html"), tmp_path)

	assert payload["code"] == "LOGIN_RISK_CONTROL"
	assert payload["recoverable"] is False


def test_heuristic_risk_message_does_not_echo_raw_text(tmp_path):
	payload = _classify(RuntimeError("403 forbidden for token=SECRET-VALUE"), tmp_path)

	assert payload["code"] == "LOGIN_RISK_CONTROL"
	assert payload["message"] == "登录请求可能触发平台风控"
	assert "SECRET-VALUE" not in str(payload)


# ── #2310：cURL 文件有界读取与格式兼容 ─────────────────────────────


def test_curl_file_rejects_non_regular_file(tmp_path):
	from pathlib import Path

	zero = Path("/dev/zero")
	if not zero.exists():
		pytest.skip("/dev/zero 不存在")
	mock_auth = MagicMock()
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, ["--curl-file", str(zero)])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "INVALID_PARAM"
	assert "普通文件" in body["error"]["message"]
	mock_auth.import_curl.assert_not_called()


def test_curl_file_rejects_oversized_text(tmp_path):
	path = tmp_path / "huge.txt"
	path.write_text("a" * (1024 * 1024 + 1), encoding="utf-8")
	mock_auth = MagicMock()
	with patch("boss_agent_cli.commands.login.AuthManager", return_value=mock_auth):
		result = _invoke(tmp_path, ["--curl-file", str(path)])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "INVALID_PARAM"
	assert "上限" in body["error"]["message"]
	mock_auth.import_curl.assert_not_called()


def test_parse_curl_tolerates_utf8_bom():
	assert parse_curl_auth("﻿" + _CURL)["cookies"] == {"wt2": "test-session"}


def test_parse_curl_accepts_uppercase_scheme():
	assert parse_curl_auth("curl HTTPS://www.zhipin.com -b 'wt2=x'")["cookies"] == {"wt2": "x"}


def test_parse_curl_rejects_ansi_c_quotes_with_specific_hint():
	with pytest.raises(ValueError, match="ANSI-C"):
		parse_curl_auth("curl $'https://www.zhipin.com' -b 'wt2=x'")


# ── #2311：构造顺序与本地 IO 文案 ──────────────────────────────────


def test_conflicting_options_checked_before_auth_manager_construction(tmp_path):
	mock_cls = MagicMock(side_effect=AssertionError("不应构造 AuthManager"))
	with patch("boss_agent_cli.commands.login.AuthManager", mock_cls):
		result = _invoke(tmp_path, ["--curl-file", "-", "--cdp"], input_text=_CURL)

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "INVALID_PARAM"
	assert "--cdp" in body["error"]["message"]
	mock_cls.assert_not_called()


def test_auth_manager_construction_failure_is_local_io_error(tmp_path):
	with patch(
		"boss_agent_cli.commands.login.AuthManager",
		side_effect=PermissionError(13, "Permission denied", str(tmp_path / "auth")),
	):
		result = _invoke(tmp_path, [])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "LOCAL_IO_ERROR"
	assert body["error"]["recoverable"] is False


def test_local_io_message_is_neutral(tmp_path):
	payload = _classify(PermissionError(13, "Permission denied", "/tmp/tmpdir"), tmp_path)

	assert payload["code"] == "LOCAL_IO_ERROR"
	assert "登录成功" not in str(payload["message"])
	assert "临时目录" in str(payload["hints"])


# ── #2312：风控 details 容器漂移 ───────────────────────────────────


def test_platform_risk_error_normalizes_non_object_details(tmp_path):
	error = PlatformRiskError("风险", details="oops")  # type: ignore[arg-type]

	assert error.details == {}
	payload = _classify(error, tmp_path)
	assert payload["code"] == "ACCOUNT_RISK"
	assert payload.get("details") is None


# ── #2313：Cookie 提取诊断脱敏 ────────────────────────────────────


def test_cookie_extract_failure_diagnostic_is_redacted():
	failures: list[str] = []

	def loader(domain_name=None, cookie_file=None):
		raise RuntimeError("Cookie: wt2=SECRET-COOKIE; __zp_stoken__=SECRET-STOKEN")

	assert _try_extract(loader, failures=failures) is None
	assert len(failures) == 1
	assert "SECRET-COOKIE" not in failures[0]
	assert "SECRET-STOKEN" not in failures[0]
	assert "[REDACTED]" in failures[0]
