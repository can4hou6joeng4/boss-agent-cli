"""Issue #3091：NO_PROXY 的 bypass 必须覆盖 httpx 的后缀匹配语义。

httpx 把 ``NO_PROXY=api.test`` 转成 ``all://*api.test``，根域与子域都直连；
Chromium 的 bypass 只按字面主机名匹配，原样转发会让 ``sub.api.test`` 仍走代理。
"""

from __future__ import annotations

from unittest.mock import patch
import os

from httpx._utils import get_environment_proxies

from boss_agent_cli.api.proxy import resolve_proxy


def _bypass(no_proxy: str) -> str:
	env = {"HTTPS_PROXY": "http://proxy.test:8080", "NO_PROXY": no_proxy}
	return resolve_proxy(env).get("bypass", "")


def _httpx_mounts(no_proxy: str) -> dict[str, str | None]:
	env = {"HTTPS_PROXY": "http://proxy.test:8080", "NO_PROXY": no_proxy}
	with patch.dict(os.environ, env, clear=True):
		return get_environment_proxies()


def test_plain_domain_covers_root_and_subdomains() -> None:
	entries = _bypass("api.test").split(",")

	assert "api.test" in entries
	assert "*.api.test" in entries
	assert "all://*api.test" in _httpx_mounts("api.test")


def test_multiple_domains_each_expanded() -> None:
	entries = _bypass("api.test, cdn.test").split(",")

	assert {"api.test", "*.api.test", "cdn.test", "*.cdn.test"} <= set(entries)


def test_ip_localhost_and_cidr_stay_exact() -> None:
	entries = _bypass("localhost,127.0.0.1,192.168.0.0/16").split(",")

	assert set(entries) == {"localhost", "127.0.0.1", "192.168.0.0/16"}


def test_wildcard_bypasses_everything() -> None:
	assert _bypass("*") == "*"
	assert _httpx_mounts("*") == {}


def test_blank_entries_ignored() -> None:
	assert _bypass(" , api.test, , ").split(",") == ["api.test", "*.api.test"]


def test_lowercase_value_still_overrides_uppercase_with_expansion() -> None:
	env = {
		"HTTPS_PROXY": "http://proxy.test:8080",
		"NO_PROXY": "upper.test",
		"no_proxy": "lower.test",
	}

	entries = resolve_proxy(env)["bypass"].split(",")
	assert {"lower.test", "*.lower.test"} <= set(entries)
	assert "upper.test" not in entries
