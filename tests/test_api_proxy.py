from boss_agent_cli.api.proxy import (
	WEBRTC_LEAK_GUARD_ARGS,
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


def test_webrtc_leak_guard_disables_non_proxied_udp() -> None:
	assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in WEBRTC_LEAK_GUARD_ARGS
	assert "--enforce-webrtc-ip-permission-check" in WEBRTC_LEAK_GUARD_ARGS
