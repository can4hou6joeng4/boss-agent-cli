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


def _response_cookie_entries(response: Any) -> list[tuple[str, str, str, str]]:
	"""响应 Cookie 的 (name, value, domain, path)，同名条目一并保留。

	不能用 ``response.cookies.items()``：jar 里同名 Cookie 多于一条时 httpx 抛
	``CookieConflict``，而平台在多域 / 重定向下确实会这样下发（Issue #2266）。
	"""
	response_cookies = getattr(response, "cookies", None)
	jar = getattr(response_cookies, "jar", None)
	if jar is None:
		# 测试替身可能只实现 items()；真实 httpx 响应总有 jar。
		if response_cookies is None:
			return []
		try:
			return [(str(name), str(value), "", "/") for name, value in response_cookies.items()]
		except Exception:
			return []
	return [(cookie.name, cookie.value, cookie.domain, cookie.path) for cookie in list(jar)]


def merge_response_cookies(http_client: Any, response: Any) -> None:
	"""把响应 Cookie 按名称替换进客户端 jar，保留 Domain / Path。

	``httpx.Client`` 已经把响应 Cookie 按域写进 jar；再用无作用域的
	``set(name, value)`` 补一份会留下第二条同名条目，之后 ``cookies.get``
	抛 ``CookieConflict``——在 recruiter 的 ``_headers_for`` 里就是请求发出前
	抛异常，写动作被误报成结果未知。
	"""
	cookies = getattr(http_client, "cookies", None)
	if cookies is None:
		return
	jar = getattr(cookies, "jar", None)
	if jar is None:
		# 只实现 set() 的测试替身：按旧行为写入，真实客户端走下面的替换路径。
		for name, value, _domain, _path in _response_cookie_entries(response):
			if value:
				cookies.set(name, value)
		return
	for name, value, domain, path in _response_cookie_entries(response):
		if not value:
			continue
		# 响应是更新的权威来源：同名旧条目（含其它域 / 路径）先删掉。
		# CookieJar.clear 只支持按 (domain, path, name) 三元组删除。
		for existing in list(jar):
			if existing.name == name:
				try:
					jar.clear(existing.domain, existing.path, name)
				except KeyError:  # 同一轮里已被删除
					pass
		cookies.set(name, value, domain=domain or "", path=path or "/")


def cookie_value(cookies: Any, name: str) -> str:
	"""按名称取 Cookie 值；同名多条时取最后一条，不抛 ``CookieConflict``。"""
	try:
		return str(cookies.get(name) or "")
	except Exception:  # httpx.CookieConflict：同一名称存在多个域 / 路径
		for cookie in reversed(list(getattr(cookies, "jar", []))):
			if cookie.name == name and cookie.value:
				return str(cookie.value)
		return ""


def add_stoken_to_get_params(method: str, kwargs: MutableMapping[str, Any], stoken: str) -> None:
	"""Inject BOSS __zp_stoken__ into GET request params in-place.

	httpx 的 ``params`` 还接受 ``None``、字符串和键值对序列；只按 dict 处理会在
	请求循环里抛 TypeError，被通用分支当成可重试的网络故障（Issue #2282）。
	"""
	if method != "GET":
		return
	params = kwargs.get("params")
	if params is None:
		kwargs["params"] = {"__zp_stoken__": stoken}
	elif isinstance(params, Mapping):
		params = dict(params)
		params["__zp_stoken__"] = stoken
		kwargs["params"] = params
	elif isinstance(params, (list, tuple)):
		kwargs["params"] = [(name, value) for name, value in params if name != "__zp_stoken__"] + [
			("__zp_stoken__", stoken)
		]
	# 其余形态（已编码的字符串 / 字节）无法原地注入，保持原样交给 httpx。


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
