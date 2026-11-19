"""API 代理与节流的边界修复（Issue #1869 #1870 #1871）。"""

from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from boss_agent_cli.api import throttle as throttle_module
from boss_agent_cli.api.proxy import parse_proxy_url, resolve_proxy
from boss_agent_cli.api.throttle import RequestThrottle


# ── Issue #1870：IPv6 字面量代理保留方括号 ──────────────────────────


@pytest.mark.parametrize(
	"raw, expected",
	[
		("http://[::1]:8080", "http://[::1]:8080"),
		("socks5://[2001:db8::1]:1080", "socks5://[2001:db8::1]:1080"),
		("http://[::1]", "http://[::1]"),
	],
)
def test_ipv6_proxy_keeps_brackets(raw: str, expected: str) -> None:
	proxy = parse_proxy_url(raw)

	assert proxy is not None
	assert proxy["server"] == expected
	assert urlsplit(proxy["server"]).hostname in {"::1", "2001:db8::1"}


def test_ipv6_proxy_server_round_trips_port() -> None:
	proxy = parse_proxy_url("http://[2001:db8::2]:3128")

	assert proxy is not None
	assert urlsplit(proxy["server"]).port == 3128
	assert urlsplit(proxy["server"]).hostname == "2001:db8::2"


def test_regular_hostname_stays_unbracketed() -> None:
	proxy = parse_proxy_url("http://proxy.example:3128")

	assert proxy is not None
	assert proxy["server"] == "http://proxy.example:3128"


# ── Issue #1869：NO_PROXY 与 scheme 语义向 httpx 对齐 ────────────────


def test_http_proxy_does_not_proxy_https_traffic() -> None:
	"""只设 HTTP_PROXY 时 httpx 的 https 请求直连，Chromium 也必须直连。"""
	assert resolve_proxy({"HTTP_PROXY": "http://proxy.example:3128"}) is None
	assert resolve_proxy({"http_proxy": "http://proxy.example:3128"}) is None


@pytest.mark.parametrize("key", ["NO_PROXY", "no_proxy"])
def test_no_proxy_is_forwarded_as_bypass(key: str) -> None:
	proxy = resolve_proxy({
		"HTTPS_PROXY": "http://proxy.example:3128",
		key: "www.zhipin.com, .zhipin.com",
	})

	assert proxy == {
		"server": "http://proxy.example:3128",
		"bypass": "www.zhipin.com, .zhipin.com",
	}


def test_no_proxy_star_bypasses_everything() -> None:
	proxy = resolve_proxy({"HTTPS_PROXY": "http://proxy.example:3128", "NO_PROXY": "*"})

	assert proxy == {"server": "http://proxy.example:3128", "bypass": "*"}


def test_absent_no_proxy_adds_no_bypass_key() -> None:
	proxy = resolve_proxy({"HTTPS_PROXY": "http://proxy.example:3128"})

	assert proxy == {"server": "http://proxy.example:3128"}


def test_blank_no_proxy_is_ignored() -> None:
	proxy = resolve_proxy({"HTTPS_PROXY": "http://proxy.example:3128", "NO_PROXY": "   "})

	assert proxy is not None
	assert "bypass" not in proxy


# ── Issue #1871：首次请求不等待，与单调时钟原点无关 ─────────────────


@pytest.mark.parametrize("monotonic_now", [0.5, 5.0, 29.0])
def test_first_wait_does_not_sleep_when_clock_origin_is_recent(monotonic_now: float) -> None:
	slept: list[float] = []

	with patch.object(throttle_module.time, "monotonic", return_value=monotonic_now), patch.object(
		throttle_module, "cancellable_sleep", lambda total, check=None: slept.append(total)
	), patch.object(throttle_module.random, "random", return_value=1.0):
		RequestThrottle((30.0, 60.0)).wait()

	assert slept == [0]


def test_second_wait_still_paces() -> None:
	slept: list[float] = []

	with patch.object(throttle_module.time, "monotonic", return_value=5.0), patch.object(
		throttle_module, "cancellable_sleep", lambda total, check=None: slept.append(total)
	), patch.object(throttle_module.random, "random", return_value=1.0), patch.object(
		throttle_module.random, "gauss", return_value=45.0
	):
		throttle = RequestThrottle((30.0, 60.0))
		throttle.wait()
		throttle.wait()

	# 第一次预占到 5 + 0，第二次 elapsed=0，必须补睡一个完整间隔。
	assert slept[0] == 0
	assert slept[1] == pytest.approx(45.0)
