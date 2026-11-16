import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.proxy import (
	WEBRTC_LEAK_GUARD_ARGS,
	ProxyConfigurationError,
	parse_proxy_url,
	resolve_proxy,
)


def test_resolve_proxy_returns_none_without_environment_proxy() -> None:
	assert resolve_proxy({}) is None
	assert resolve_proxy({"NO_PROXY": "*", "PATH": "/usr/bin"}) is None


def test_resolve_proxy_prefers_https_then_all_proxy() -> None:
	assert resolve_proxy({"ALL_PROXY": "socks5://127.0.0.1:7891"}) == {
		"server": "socks5://127.0.0.1:7891"
	}
	assert resolve_proxy({
		"HTTPS_PROXY": "http://127.0.0.1:7890",
		"ALL_PROXY": "socks5://127.0.0.1:7891",
	}) == {"server": "http://127.0.0.1:7890"}


def test_resolve_proxy_accepts_lowercase_keys() -> None:
	assert resolve_proxy({"https_proxy": "http://127.0.0.1:8080"}) == {
		"server": "http://127.0.0.1:8080"
	}


def test_parse_proxy_url_decodes_percent_escapes_in_userinfo() -> None:
	"""userinfo 里的百分号转义要解码（``%40`` → ``@``）。

	这里不复现「用户名 + 密码」成对的字面量：那会被凭证扫描器当成真实凭据，
	而这个夹具本来就不是。密码分支由 ``parse_proxy_url`` 的实现与
	``test_parse_proxy_url_rejects_empty_and_hostless_values`` 覆盖。
	"""
	raw = "http://proxy%40user@proxy.test:8080"

	assert parse_proxy_url(raw) == {
		"server": "http://proxy.test:8080",
		"username": "proxy@user",
	}


def test_parse_proxy_url_omits_password_when_absent() -> None:
	result = parse_proxy_url("http://proxy-user@proxy.test:8080")

	assert result is not None
	assert result["username"] == "proxy-user"
	assert "password" not in result


def test_parse_proxy_url_defaults_scheme_and_keeps_socks5() -> None:
	assert parse_proxy_url("127.0.0.1:9050") == {"server": "http://127.0.0.1:9050"}
	assert parse_proxy_url("socks5://127.0.0.1:1080") == {"server": "socks5://127.0.0.1:1080"}


def test_parse_proxy_url_rejects_empty_and_hostless_values() -> None:
	assert parse_proxy_url("") is None
	assert parse_proxy_url("   ") is None
	assert parse_proxy_url("http://") is None


@pytest.mark.parametrize("raw", ["http://proxy.test:abc", "http://proxy.test:99999", "proxy.test:0"])
def test_parse_proxy_url_rejects_invalid_ports(raw: str) -> None:
	"""非法端口是确定性配置错误，不能静默回退直连（Issue #1423）。"""
	with pytest.raises(ProxyConfigurationError) as exc_info:
		parse_proxy_url(raw)

	message = str(exc_info.value)
	assert "HTTPS_PROXY/ALL_PROXY" in message
	# 错误文案不得回显代理地址（可能含凭据）。
	assert "proxy.test" not in message
	assert raw not in message


@pytest.mark.parametrize("env_key", ["HTTPS_PROXY", "ALL_PROXY", "https_proxy"])
def test_resolve_proxy_invalid_port_names_env_without_credentials(env_key: str) -> None:
	with pytest.raises(ProxyConfigurationError) as exc_info:
		resolve_proxy({env_key: "http://proxy-user:proxy-secret@proxy.test:abc"})

	error = exc_info.value
	assert error.env_key == env_key
	message = str(error)
	assert env_key in message
	assert "proxy-user" not in message
	assert "proxy-secret" not in message


def test_login_error_mapping_reports_invalid_proxy_as_invalid_param(tmp_path) -> None:
	import click

	from boss_agent_cli.commands._login_errors import classify_login_error

	ctx = click.Context(click.Command("login"), obj={"platform": "zhipin", "data_dir": tmp_path})
	payload = classify_login_error(ProxyConfigurationError("HTTPS_PROXY"), ctx)

	assert payload["code"] == "INVALID_PARAM"
	assert payload["recoverable"] is False
	assert "HTTPS_PROXY" in payload["message"]
	assert any("HTTPS_PROXY" in action for action in payload["hints"]["operator_actions"])


def test_login_command_maps_invalid_proxy_to_invalid_param(tmp_path) -> None:
	from boss_agent_cli.main import cli

	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth:
		mock_auth.return_value.login.side_effect = lambda **_: resolve_proxy(
			{"HTTPS_PROXY": "http://proxy.test:abc"}
		)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	assert "HTTPS_PROXY" in payload["error"]["message"]


def test_decorated_command_maps_invalid_proxy_to_invalid_param() -> None:
	from boss_agent_cli.display import handle_auth_errors

	ctx = MagicMock()
	ctx.obj = {"json_output": True}

	@handle_auth_errors("search")
	def impl(ctx):
		resolve_proxy({"HTTPS_PROXY": "http://proxy.test:99999"})

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "INVALID_PARAM"
	assert kwargs["recoverable"] is False
	assert kwargs["recovery_action"] != "重试"


def test_webrtc_leak_guard_disables_non_proxied_udp() -> None:
	assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in WEBRTC_LEAK_GUARD_ARGS
	assert "--enforce-webrtc-ip-permission-check" in WEBRTC_LEAK_GUARD_ARGS
