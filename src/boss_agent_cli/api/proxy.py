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

class PlaywrightProxy(TypedDict, total=False):
	"""Subset of Playwright's ``ProxySettings`` this project sets."""

	server: str
	bypass: str | None
	username: str | None
	password: str | None


PROXY_ENV_KEYS: tuple[str, ...] = (
	"HTTPS_PROXY",
	"https_proxy",
	"ALL_PROXY",
	"all_proxy",
	"HTTP_PROXY",
	"http_proxy",
)

# Chromium keeps non-proxied UDP (STUN) out of the proxy by default, which
# exposes the real address even when --proxy-server is honoured.
WEBRTC_LEAK_GUARD_ARGS: tuple[str, ...] = (
	"--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
	"--enforce-webrtc-ip-permission-check",
)


def resolve_proxy(environ: Mapping[str, str] | None = None) -> PlaywrightProxy | None:
	"""Return a Playwright-shaped proxy config for the environment's proxy.

	``None`` means "no proxy configured", which leaves Chromium on a direct
	connection rather than inventing one.
	"""
	source = os.environ if environ is None else environ
	raw = ""
	for key in PROXY_ENV_KEYS:
		value = source.get(key, "").strip()
		if value:
			raw = value
			break
	if not raw:
		return None
	return parse_proxy_url(raw)


def parse_proxy_url(raw: str) -> PlaywrightProxy | None:
	"""Convert ``scheme://[user:pass@]host:port`` into a Playwright proxy dict."""
	raw = raw.strip()
	if not raw:
		return None
	if "://" not in raw:
		raw = f"http://{raw}"
	try:
		parts = urlsplit(raw)
	except ValueError:
		return None
	if not parts.hostname:
		return None
	scheme = parts.scheme or "http"
	port = parts.port
	server = f"{scheme}://{parts.hostname}"
	if port:
		server = f"{server}:{port}"
	proxy: PlaywrightProxy = {"server": server}
	if parts.username:
		proxy["username"] = unquote(parts.username)
	if parts.password:
		proxy["password"] = unquote(parts.password)
	return proxy
