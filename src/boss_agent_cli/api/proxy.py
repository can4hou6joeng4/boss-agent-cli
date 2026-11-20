"""Resolve one egress proxy for every channel that talks to the platform.

The httpx channel honours the environment (``trust_env=True``) while Chromium
ignores ``HTTPS_PROXY``/``ALL_PROXY`` entirely. Left alone, a user with a proxy
configured logs in from their real address and then reads with the proxy — same
account, same cookie jar, two exits within seconds. This module gives both
channels the same answer so a session keeps one egress.

Chromium also leaks the real address over WebRTC UDP unless told otherwise;
:data:`WEBRTC_LEAK_GUARD_ARGS` closes that even when no proxy is configured.
"""

from __future__ import annotations

import os
from typing import Mapping, TypedDict
from urllib.parse import unquote, urlsplit


class ProxyConfigurationError(ValueError):
	"""代理环境变量存在但无法解析成可用代理（Issue #1423 / #1486）。

	把非法配置静默当成「没有代理」会让 Chromium 直连并泄露真实出口，所以这里
	抛 typed error，由命令层统一映射为 ``INVALID_PARAM``（确定性配置错误，
	不是可重试的网络故障）。文案只点名环境变量，绝不回显 URL 或其中的凭据。
	"""

	def __init__(self, env_key: str | None = None) -> None:
		self.env_key = env_key
		label = env_key or "HTTPS_PROXY/ALL_PROXY"
		super().__init__(
			f"代理配置无效：{label} 的代理地址或端口无法解析（地址必须包含 host，"
			"端口必须是 1-65535 的整数）；"
			"修正或清除 HTTPS_PROXY/ALL_PROXY 等代理环境变量后重试，不会回退为直连"
		)


class PlaywrightProxy(TypedDict, total=False):
	"""Subset of Playwright's ``ProxySettings`` this project sets."""

	server: str
	bypass: str | None
	username: str | None
	password: str | None


#: Chromium 只按 https 出口选代理：平台 API 全是 https，httpx 在 trust_env 下
#: 也不会把 HTTP_PROXY 用于 https 请求，Chromium 若继续用它就会与 API 通道
#: 分叉出第二个出口（Issue #1869）。
PROXY_ENV_KEYS: tuple[str, ...] = (
	"HTTPS_PROXY",
	"https_proxy",
	"ALL_PROXY",
	"all_proxy",
)

_NO_PROXY_ENV_KEYS: tuple[str, ...] = ("NO_PROXY", "no_proxy")

# Chromium keeps non-proxied UDP (STUN) out of the proxy by default, which
# exposes the real address even when --proxy-server is honoured.
WEBRTC_LEAK_GUARD_ARGS: tuple[str, ...] = (
	"--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
	"--enforce-webrtc-ip-permission-check",
)


def resolve_proxy(environ: Mapping[str, str] | None = None) -> PlaywrightProxy | None:
	"""Return a Playwright-shaped proxy config for the environment's proxy.

	``None`` means "no proxy configured" (the value is unset or blank), which
	leaves Chromium on a direct connection rather than inventing one. A
	non-blank value that cannot be parsed raises :class:`ProxyConfigurationError`
	instead of silently turning into ``None`` — otherwise Chromium would go
	direct and leak the real egress while httpx keeps using the broken proxy
	(Issue #1486).

	Only https-capable proxy variables are considered and ``NO_PROXY`` is
	forwarded as Chromium's ``bypass`` list, mirroring what httpx's
	``trust_env=True`` does for https requests (Issue #1869).
	"""
	source = os.environ if environ is None else environ
	raw = ""
	key = ""
	for key in PROXY_ENV_KEYS:
		value = source.get(key, "").strip()
		if value:
			raw = value
			break
	if not raw:
		return None
	proxy = parse_proxy_url(raw, env_key=key)
	if proxy is not None:
		bypass = _no_proxy_value(source)
		if bypass:
			# Chromium 的 ``bypass`` 与 httpx 的 NO_PROXY 一样按目标主机生效：
			# 原样传下去，两个通道对同一域名同时直连（Issue #1869）。
			proxy["bypass"] = bypass
	return proxy


def _no_proxy_value(source: Mapping[str, str]) -> str:
	for key in _NO_PROXY_ENV_KEYS:
		value = source.get(key, "").strip()
		if value:
			return value
	return ""


#: Chromium / Playwright 能使用的代理 scheme（Issue #1920）。
_PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5"})


def parse_proxy_url(raw: str, *, env_key: str | None = None) -> PlaywrightProxy | None:
	"""Convert ``scheme://[user:pass@]host:port`` into a Playwright proxy dict.

	只有空白字符串返回 ``None``。非空但缺少 host（如 ``http://``、
	``http://:8080``）或端口非法（非整数 / 超出 1-65535）时抛
	:class:`ProxyConfigurationError`，而不是静默返回 ``None``：后者会让
	Chromium 直连，导致 httpx 与浏览器两个通道出口不一致（Issue #1423 / #1486）。
	"""
	raw = raw.strip()
	if not raw:
		return None
	if "://" not in raw:
		raw = f"http://{raw}"
	try:
		parts = urlsplit(raw)
		# ``parts.port`` 的求值必须在 try 内：``http://host:abc`` 与
		# ``http://host:99999`` 都在这里抛 ValueError，此前落在 try 之外，
		# 变成裸 ValueError 并被命令层兜成可重试的 NETWORK_ERROR。
		port = parts.port
	except ValueError:
		raise ProxyConfigurationError(env_key) from None
	if not parts.hostname:
		# 非空但解析不出 host（``http://``、``http://:8080``、``://nohost``）：
		# 静默当成「没有代理」会让 Chromium 直连并泄露真实出口（Issue #1486）。
		raise ProxyConfigurationError(env_key)
	scheme = parts.scheme or "http"
	# Playwright / Chromium 只支持 http(s) 与 SOCKS 代理：ftp / file /
	# javascript 之类的 scheme 不可能成为可用代理，旧实现却原样返回，把错误
	# 推迟到启动 / 请求阶段，绕过确定性的参数错误出口（Issue #1920）。
	if scheme not in _PROXY_SCHEMES:
		raise ProxyConfigurationError(env_key)
	# ``urlsplit().hostname`` 会去掉 IPv6 方括号，拼回时必须补上，否则
	# ``http://[::1]:8080`` 变成无法解析的 ``http://::1:8080``（Issue #1870）。
	host = parts.hostname
	# 空白 / 控制字符 / URL 分隔符不可能出现在合法 host 里；放行会让配置错误
	# 直到浏览器启动才暴露（Issue #1920）。反斜杠也要拒绝：Chromium 对特殊
	# scheme 把 ``\`` 当路径分隔符，``ho\st`` 实际按主机 ``ho`` 建立连接，出口
	# 与这里校验的值不一致（Issue #2281）。
	if _illegal_host_characters(host):
		raise ProxyConfigurationError(env_key)
	# 百分号编码的 host 是否解码取决于客户端；解码后仍含非法字符即拒绝。
	if "%" in host and ":" not in host and _illegal_host_characters(unquote(host)):
		raise ProxyConfigurationError(env_key)
	if ":" in host:
		host = f"[{host}]"
	server = f"{scheme}://{host}"
	if port is not None:
		if port <= 0:
			# 显式 :0 不是可用端口；静默省略会变成 scheme 的默认端口。
			raise ProxyConfigurationError(env_key)
		server = f"{server}:{port}"
	proxy: PlaywrightProxy = {"server": server}
	if parts.username:
		proxy["username"] = unquote(parts.username)
	if parts.password:
		proxy["password"] = unquote(parts.password)
	return proxy


def _illegal_host_characters(host: str) -> bool:
	"""host 里出现这些字符就不可能是合法代理主机。"""
	return any(char.isspace() or ord(char) < 0x20 or char in "/?#@\\|" for char in host)
