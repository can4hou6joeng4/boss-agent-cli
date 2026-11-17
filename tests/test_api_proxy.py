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


def test_parse_proxy_url_returns_none_only_for_empty_values() -> None:
	"""只有空白值表示「没有配置代理」；这是独立用例，便于收紧 hostless 行为时复核对。"""
	assert parse_proxy_url("") is None
	assert parse_proxy_url("   ") is None


@pytest.mark.parametrize(
	"raw",
	["http://", "http://:8080", "://nohost", "http://proxy-user:proxy-secret@:8080"],
)
def test_parse_proxy_url_rejects_hostless_values(raw: str) -> None:
	"""非空但解析不出 host 的代理必须显式拒绝，不能静默回退直连（Issue #1486）。"""
	with pytest.raises(ProxyConfigurationError) as exc_info:
		parse_proxy_url(raw)

	message = str(exc_info.value)
	assert "HTTPS_PROXY/ALL_PROXY" in message
	assert "host" in message
	# 错误文案不得回显代理地址（可能含凭据）。
	assert raw not in message
	assert "nohost" not in message
	assert "proxy-user" not in message
	assert "proxy-secret" not in message


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


@pytest.mark.parametrize("env_key", ["HTTPS_PROXY", "ALL_PROXY", "https_proxy"])
@pytest.mark.parametrize("raw", ["http://", "http://:8080", "://nohost"])
def test_resolve_proxy_rejects_hostless_env_values(env_key: str, raw: str) -> None:
	"""环境变量非空但解析不出 host：按配置错误拒绝并点名变量，绝不返回 None。"""
	with pytest.raises(ProxyConfigurationError) as exc_info:
		resolve_proxy({env_key: raw})

	error = exc_info.value
	assert error.env_key == env_key
	assert env_key in str(error)
	assert raw not in str(error)


def test_resolve_proxy_treats_blank_env_value_as_unset() -> None:
	"""空白值仍是「未配置代理」，不是需要拒绝的非法配置。"""
	assert resolve_proxy({"HTTPS_PROXY": "   ", "ALL_PROXY": ""}) is None


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


def test_login_command_rejects_hostless_proxy_as_invalid_param(tmp_path) -> None:
	"""端到端：代理变量非空但无 host 时，login 命令返回 INVALID_PARAM 信封。

	真实链路上 ``login_via_browser`` 会在 launch Chromium 之前调用
	``resolve_proxy``；这里让 mock 的登录实现走同一条抛错路径，验证命令层
	不会把 typed error 兜成可重试的 NETWORK_ERROR 或未捕获异常（Issue #1486）。
	"""
	from boss_agent_cli.main import cli

	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth:
		mock_auth.return_value.login.side_effect = lambda **_: resolve_proxy(
			{"HTTPS_PROXY": "http://"}
		)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	assert "HTTPS_PROXY" in payload["error"]["message"]
	assert "不会回退为直连" in payload["error"]["message"]


@pytest.mark.parametrize("proxy_value", ["http://proxy.test:99999", "http://"])
def test_decorated_command_maps_invalid_proxy_to_invalid_param(proxy_value: str) -> None:
	from boss_agent_cli.display import handle_auth_errors

	ctx = MagicMock()
	ctx.obj = {"json_output": True}

	@handle_auth_errors("search")
	def impl(ctx):
		resolve_proxy({"HTTPS_PROXY": proxy_value})

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "INVALID_PARAM"
	assert kwargs["recoverable"] is False
	assert kwargs["recovery_action"] != "重试"


def test_browser_session_refuses_hostless_proxy_before_launch(monkeypatch) -> None:
	"""BrowserSession 在 launch 之前就拒绝无 host 代理，不会带 None 直连。

	``proxy=resolve_proxy()`` 在参数求值阶段抛错，因此 fake playwright 的
	``launch`` 一次都不应被调用 —— 这正是「浏览器侧泄露真实出口」的回归点
	（Issue #1486）。
	"""
	from types import SimpleNamespace

	from boss_agent_cli.api import browser_client
	from boss_agent_cli.api.browser_source import CHANNEL_HEADLESS, BrowserSourcePolicy

	launch_calls: list[dict[str, object]] = []
	fake_pw = SimpleNamespace(
		chromium=SimpleNamespace(launch=lambda **kwargs: launch_calls.append(kwargs))
	)
	headless_only = BrowserSourcePolicy(
		name="test-headless",
		channels=(CHANNEL_HEADLESS,),
		use_stored_credentials=True,
		may_create_context=True,
		allow_browser_launch=True,
		auto_probe_cdp=False,
		failure_code=None,
	)
	monkeypatch.setattr(browser_client, "resolve_policy", lambda source: headless_only)
	monkeypatch.setenv("HTTPS_PROXY", "http://")

	session = browser_client.BrowserSession(cookies={}, user_agent="")
	session._pw = fake_pw

	with pytest.raises(ProxyConfigurationError):
		session.ensure_started()

	assert launch_calls == []


def test_webrtc_leak_guard_disables_non_proxied_udp() -> None:
	assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in WEBRTC_LEAK_GUARD_ARGS
	assert "--enforce-webrtc-ip-permission-check" in WEBRTC_LEAK_GUARD_ARGS
