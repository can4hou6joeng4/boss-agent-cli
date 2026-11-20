"""响应 Cookie 合并不得留下同名条目（Issue #2266）。

httpx.Client 已经把响应 Cookie 按域写进 jar；再补一份无作用域副本会让
``cookies.get(name)`` 抛 ``CookieConflict``——在写动作的请求头构造阶段就是
「发送前抛异常」，命令会把确定未发出的请求报成结果未知。全部离线。
"""

from __future__ import annotations

import httpx

from boss_agent_cli.api.httpx_helpers import cookie_value, merge_response_cookies


def _client(handler) -> httpx.Client:
	return httpx.Client(transport=httpx.MockTransport(handler))


def _set_cookie(*values: str):
	def handler(request: httpx.Request) -> httpx.Response:
		return httpx.Response(200, headers=[("set-cookie", value) for value in values], json={"code": 0})

	return handler


def _names(cookies: httpx.Cookies) -> list[str]:
	return [cookie.name for cookie in cookies.jar]


def test_merge_after_client_extraction_keeps_a_single_cookie() -> None:
	with _client(_set_cookie("bst=rotated; Path=/")) as client:
		response = client.get("https://www.zhipin.com/wapi/x")
		merge_response_cookies(client, response)

		assert _names(client.cookies) == ["bst"]
		assert client.cookies.get("bst") == "rotated"


def test_merge_accepts_same_name_for_two_domains() -> None:
	with _client(_set_cookie("sid=A; Path=/", "sid=B; Domain=.zhipin.com; Path=/")) as client:
		response = client.get("https://www.zhipin.com/wapi/x")
		merge_response_cookies(client, response)

		assert _names(client.cookies) == ["sid"]
		assert client.cookies.get("sid") in {"A", "B"}


def test_merge_replaces_previous_cookie_instead_of_appending() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		value = "first" if request.url.path.endswith("one") else "second"
		return httpx.Response(200, headers=[("set-cookie", f"bst={value}; Path=/")])

	with _client(handler) as client:
		first = client.get("https://www.zhipin.com/one")
		merge_response_cookies(client, first)
		second = client.get("https://www.zhipin.com/two")
		merge_response_cookies(client, second)

		assert _names(client.cookies) == ["bst"]
		assert client.cookies.get("bst") == "second"


def test_merge_does_not_resurrect_a_cookie_the_server_deleted() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		if request.url.path.endswith("login"):
			return httpx.Response(200, headers=[("set-cookie", "bst=rotated; Path=/")])
		return httpx.Response(200, headers=[("set-cookie", "bst=; Max-Age=0; Path=/")])

	with _client(handler) as client:
		merge_response_cookies(client, client.get("https://www.zhipin.com/login"))
		assert client.cookies.get("bst") == "rotated"
		merge_response_cookies(client, client.get("https://www.zhipin.com/logout"))

		assert _names(client.cookies) == []


def test_cookie_value_survives_manual_duplicates() -> None:
	with _client(_set_cookie("bst=first; Path=/")) as client:
		merge_response_cookies(client, client.get("https://www.zhipin.com/wapi/x"))
		# 模拟历史遗留的重复条目（例如旧版本写下的无作用域副本）。
		client.cookies.set("bst", "legacy")

		assert cookie_value(client.cookies, "bst") == "legacy"


def test_cookie_value_returns_empty_for_missing_or_empty_jar() -> None:
	assert cookie_value(httpx.Cookies(), "bst") == ""
