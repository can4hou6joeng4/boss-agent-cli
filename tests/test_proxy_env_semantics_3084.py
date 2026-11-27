"""Issue #3084：代理环境变量必须与 httpx（urllib 两遍算法）同语义。

httpx 的 ``trust_env=True`` 走 ``urllib.request.getproxies()``：小写拼写覆盖
大写拼写、小写空值清除该 scheme，``NO_PROXY`` 同理。旧实现按固定顺序取
第一个非空值，让 API 通道与浏览器通道得到不同代理 / 不同绕过边界。
"""

from __future__ import annotations

from unittest.mock import patch
import os

from httpx._utils import get_environment_proxies
import pytest

from boss_agent_cli.api.proxy import ProxyConfigurationError, resolve_proxy

def _httpx_proxy(env: dict[str, str]) -> str | None:
	"""httpx 对平台 https 请求会挂载的代理（考虑 https 优先于 all）。"""
	mounts = _httpx_bypass_mounts(env)
	return mounts.get("https://") or mounts.get("all://")


def _httpx_bypass_mounts(env: dict[str, str]) -> dict[str, str | None]:
	with patch.dict(os.environ, env, clear=True):
		return get_environment_proxies()


def test_lowercase_proxy_spelling_overrides_uppercase() -> None:
	env = {
		"HTTPS_PROXY": "http://proxy-upper.test:8080",
		"https_proxy": "http://proxy-lower.test:8080",
	}

	assert resolve_proxy(env) == {"server": "http://proxy-lower.test:8080"}
	assert _httpx_proxy(env) == "http://proxy-lower.test:8080"


def test_lowercase_blank_clears_uppercase_proxy() -> None:
	env = {"HTTPS_PROXY": "http://proxy-upper.test:8080", "https_proxy": ""}

	assert resolve_proxy(env) is None
	assert _httpx_proxy(env) is None


def test_uppercase_blank_does_not_clear_lowercase_proxy() -> None:
	env = {"HTTPS_PROXY": "", "https_proxy": "http://proxy-lower.test:8080"}

	assert resolve_proxy(env) == {"server": "http://proxy-lower.test:8080"}
	assert _httpx_proxy(env) == "http://proxy-lower.test:8080"


def test_lowercase_all_proxy_spelling_overrides_uppercase() -> None:
	env = {
		"ALL_PROXY": "socks5://proxy-upper.test:1080",
		"all_proxy": "socks5://proxy-lower.test:1080",
	}

	assert resolve_proxy(env) == {"server": "socks5://proxy-lower.test:1080"}
	assert _httpx_proxy(env) == "socks5://proxy-lower.test:1080"


def test_https_spelling_still_wins_over_all_proxy() -> None:
	env = {
		"https_proxy": "http://proxy-https.test:8080",
		"ALL_PROXY": "http://proxy-all.test:8080",
	}

	assert resolve_proxy(env) == {"server": "http://proxy-https.test:8080"}
	assert _httpx_proxy(env) == "http://proxy-https.test:8080"


def test_no_proxy_lowercase_spelling_overrides_uppercase() -> None:
	env = {
		"HTTPS_PROXY": "http://proxy.test:8080",
		"NO_PROXY": "upper.test",
		"no_proxy": "lower.test",
	}

	assert resolve_proxy(env)["bypass"] == "lower.test,*.lower.test"
	assert "all://*lower.test" in _httpx_bypass_mounts(env)
	assert "all://*upper.test" not in _httpx_bypass_mounts(env)


def test_no_proxy_lowercase_blank_clears_uppercase() -> None:
	env = {
		"HTTPS_PROXY": "http://proxy.test:8080",
		"NO_PROXY": "upper.test",
		"no_proxy": "",
	}

	assert resolve_proxy(env) == {"server": "http://proxy.test:8080"}
	assert "all://*upper.test" not in _httpx_bypass_mounts(env)


def test_no_proxy_uppercase_blank_does_not_clear_lowercase() -> None:
	env = {
		"HTTPS_PROXY": "http://proxy.test:8080",
		"NO_PROXY": "",
		"no_proxy": "lower.test",
	}

	assert resolve_proxy(env)["bypass"] == "lower.test,*.lower.test"
	assert "all://*lower.test" in _httpx_bypass_mounts(env)


def test_invalid_lowercase_proxy_still_raises_typed_error() -> None:
	with pytest.raises(ProxyConfigurationError) as exc:
		resolve_proxy({"https_proxy": "http://proxy.test:abc"})

	assert "https_proxy" in str(exc.value)


def test_blank_proxy_value_still_means_unset() -> None:
	assert resolve_proxy({"HTTPS_PROXY": "   ", "ALL_PROXY": ""}) is None
