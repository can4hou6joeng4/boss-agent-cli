"""Small helpers shared by httpx-backed API clients."""

from __future__ import annotations

import sys
from typing import Any, Mapping, MutableMapping


def browser_headers(
	default_headers: Mapping[str, str],
	token: Mapping[str, Any],
	*,
	include_client_id: bool = False,
	default_platform: str | None = None,
) -> dict[str, str]:
	"""Build browser-like headers from persisted auth metadata."""
	headers = dict(default_headers)
	if ua := token.get("user_agent"):
		headers["User-Agent"] = str(ua)
	if include_client_id and (client_id := token.get("x_zp_client_id") or token.get("client_id")):
		headers["x-zp-client-id"] = str(client_id)
	platform_header = sec_ch_ua_platform(default_platform=default_platform)
	if platform_header:
		headers["sec-ch-ua-platform"] = platform_header
	return headers


def sec_ch_ua_platform(*, default_platform: str | None = None) -> str | None:
	"""Return the current platform value used by browser-style headers."""
	if sys.platform == "win32":
		return '"Windows"'
	if sys.platform == "linux":
		return '"Linux"'
	if default_platform:
		return f'"{default_platform}"'
	return None


def referer_header(url: str, referer_map: Mapping[str, str], fallback: str) -> dict[str, str]:
	"""Return a Referer header for a request URL."""
	return {"Referer": referer_map.get(url, fallback)}


def merge_response_cookies(http_client: Any, response: Any) -> None:
	"""Copy non-empty response cookies onto an existing httpx client."""
	for name, value in response.cookies.items():
		if value:
			http_client.cookies.set(name, value)


def add_stoken_to_get_params(method: str, kwargs: MutableMapping[str, Any], stoken: str) -> None:
	"""Inject BOSS __zp_stoken__ into GET request params in-place."""
	if method != "GET":
		return
	params = kwargs.get("params", {})
	params["__zp_stoken__"] = stoken
	kwargs["params"] = params
