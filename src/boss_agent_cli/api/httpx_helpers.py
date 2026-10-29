"""Small helpers shared by httpx-backed API clients."""

from __future__ import annotations

import re
import weakref
from typing import Any, Callable, Mapping, MutableMapping

# Client-hint platform values keyed by a User-Agent substring, checked in order.
# Order matters: an Android UA also contains "Linux", and a ChromeOS UA too.
_UA_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
	("Windows NT", '"Windows"'),
	("Android", '"Android"'),
	("CrOS", '"Chrome OS"'),
	("iPhone", '"iOS"'),
	("iPad", '"iOS"'),
	("Macintosh", '"macOS"'),
	("Linux", '"Linux"'),
)

_CHROME_MAJOR = re.compile(r"Chrome/(\d+)")
_ENTRY_VERSION = re.compile(r'v="\d+"')
_BRAND_ENTRY = re.compile(r'^(?P<brand>"[^"]*");\s*v="(?P<version>\d+)"$')

# Headers the BOSS page attaches to its own XHRs. They are generated in-page and
# are stable for the life of a session, so they are captured from a real request
# and replayed; see auth/page_identity.py.
PAGE_IDENTITY_HEADERS: tuple[str, ...] = ("token", "zp_token")


def browser_headers(
	default_headers: Mapping[str, str],
	token: Mapping[str, Any],
) -> dict[str, str]:
	"""Build browser-like headers that agree with the User-Agent they carry.

	Client hints are derived from the effective User-Agent rather than the host
	OS. Deriving them from the host while keeping a fixed macOS User-Agent made
	the two disagree on Windows and Linux (``User-Agent: ... Macintosh ...`` next
	to ``sec-ch-ua-platform: "Windows"``), which is a contradiction no real
	browser produces.
	"""
	headers = dict(default_headers)
	if ua := token.get("user_agent"):
		headers["User-Agent"] = str(ua)
	user_agent = headers.get("User-Agent", "")
	if platform_hint := sec_ch_ua_platform(user_agent):
		headers["sec-ch-ua-platform"] = platform_hint
	if brand_template := headers.get("sec-ch-ua"):
		headers["sec-ch-ua"] = sec_ch_ua_brands(user_agent, str(brand_template))
	headers["sec-ch-ua-mobile"] = "?1" if "Mobile" in user_agent else "?0"
	for name in PAGE_IDENTITY_HEADERS:
		if value := token.get(name):
			headers[name] = str(value)
	return headers


def sec_ch_ua_platform(user_agent: str = "") -> str | None:
	"""Return the client-hint platform implied by ``user_agent``.

	Returns ``None`` when the User-Agent does not identify a known platform, so
	callers keep whatever the base headers already declared instead of inventing
	a value that could contradict them.
	"""
	for marker, hint in _UA_PLATFORM_HINTS:
		if marker in user_agent:
			return hint
	return None


_TRACE_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def new_trace_id(rand: Callable[[int], int] | None = None) -> str:
	"""Return a request-scoped ``traceid`` in the shape the real page produces.

	Observed samples are ``F-`` followed by 16–36 lowercase alphanumerics, and no
	two requests ever share a value. This function never claims to reproduce the
	page's generator: it emits a value of the same shape so the header is present
	on the requests where the page would have set one.
	"""
	pick = rand if rand is not None else (lambda bound: __import__("secrets").randbelow(bound))
	head = "".join(_TRACE_ALPHABET[pick(10)] for _ in range(8))
	tail = "".join(_TRACE_ALPHABET[pick(36)] for _ in range(16))
	return f"F-{head}{tail}"


def sec_ch_ua_brands(user_agent: str, template: str) -> str:
	"""Align the brand versions in ``template`` with the User-Agent's major version.

	Greasing brands (for example ``"Not;A=Brand";v="99"``) keep their own
	version: they are deliberately unrelated to the browser version.
	"""
	match = _CHROME_MAJOR.search(user_agent)
	if not match or not template:
		return template
	major = match.group(1)
	entries = []
	for raw_entry in template.split(","):
		entry = raw_entry.strip()
		if not entry:
			continue
		parsed = _BRAND_ENTRY.match(entry)
		if parsed and "not" in parsed["brand"].lower() and "brand" in parsed["brand"].lower():
			entries.append(entry)
		else:
			entries.append(_ENTRY_VERSION.sub(f'v="{major}"', entry))
	return ", ".join(entries)


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


def make_client_registry() -> tuple[weakref.WeakSet[Any], Callable[[], None]]:
	"""建一个 WeakSet 注册表 + atexit 安全的 close-all 回调；供各 client 复用。"""
	registry: weakref.WeakSet[Any] = weakref.WeakSet()

	def close_all() -> None:
		for client in list(registry):
			try:
				client.close()
			except Exception:
				pass

	return registry, close_all
