"""系统代理（`ALL_PROXY` / `HTTPS_PROXY`）下的 httpx 通道回归测试。

背景（issue #412）：全仓四处 `httpx.Client` 构造都用默认 `trust_env=True`，
即刻意尊重用户的系统代理——对这个项目的目标用户群来说常开代理是常态。
但 httpx 只有装了 `socksio` 才支持 `socks5://` scheme，否则在**构造 client 时**
就抛 `ImportError`，于是设了 `ALL_PROXY=socks5://...` 的用户，每一条走 httpx 的
命令都会失败；而那个 ImportError 会被 `display.handle_auth_errors` 的兜底
`except Exception` 转成 `NETWORK_ERROR` + `recovery_action="重试"`——错误码与恢复
动作双错，且这是个永远不会因重试而改变的本地环境问题。

CI runner 环境���净、没有代理变量，所以这条路径在门禁上永远是绿的。下面的测试
显式注入代理环境变量，把它变成可观测的。
"""

import httpx
import pytest

_SOCKS_ENVS = ("ALL_PROXY", "all_proxy")
_PROXY_ENVS = (*_SOCKS_ENVS, "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")


@pytest.fixture
def _clean_proxy_env(monkeypatch):
	"""开发机上可能本来就设了代理，先清干净再逐条注入，避免测试受环境影响。"""
	for name in _PROXY_ENVS:
		monkeypatch.delenv(name, raising=False)
	return monkeypatch


def test_socks_proxy_support_is_installed():
	"""`socksio` 必须随 `httpx[socks]` 一起装上。

	删掉 pyproject 里的 `[socks]` extra 这条会红——否则那个删除会静默通过，
	而 CI 环境永远没有代理变量、发现不了。
	"""
	pytest.importorskip("socksio", reason="httpx[socks] 未生效：socksio 没装上")


@pytest.mark.parametrize("scheme", ["socks5", "socks5h", "http"])
def test_httpx_client_constructs_under_a_system_proxy(_clean_proxy_env, scheme):
	"""设了系统代理时 `httpx.Client` 必须能构造出来，不得抛 ImportError。

	`socks5` 是真正的回归点：没有 socksio 时这一条会
	`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed.`
	`http` 是对照组——它从来不需要 socksio，用来证明失败确实来自 scheme 而非
	「设了代理」这件事本身。
	"""
	_clean_proxy_env.setenv("ALL_PROXY", f"{scheme}://127.0.0.1:7890")

	# 只构造、不发请求：ImportError 发生在构造期，无需真的连出去。
	client = httpx.Client(base_url="https://www.zhipin.com", timeout=1)
	client.close()


def test_base_http_client_constructs_under_a_socks_proxy(_clean_proxy_env, monkeypatch):
	"""仓库自己的 httpx 通道在 SOCKS 代理下也必须能起来。

	直接测 `_BaseHttpClient._get_client()` 而不只是裸 `httpx.Client`：真正会被
	用户命中的是这条路径，而它额外带了 base_url / cookies / headers。
	"""
	from boss_agent_cli.api.client import BossClient

	_clean_proxy_env.setenv("ALL_PROXY", "socks5://127.0.0.1:7890")

	class _StubAuth:
		def get_token(self):
			return {"cookies": {"wt2": "x"}, "user_agent": "ua"}

	client = BossClient(_StubAuth())
	try:
		assert isinstance(client._get_client(), httpx.Client)
	finally:
		client.close()
