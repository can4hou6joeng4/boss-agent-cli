from boss_agent_cli.api.httpx_helpers import (
	add_stoken_to_get_params,
	browser_headers,
	merge_response_cookies,
	referer_header,
)


class _CookieJar:
	def __init__(self, values: dict[str, str]) -> None:
		self.values = dict(values)

	def items(self):
		return self.values.items()

	def set(self, name: str, value: str) -> None:
		self.values[name] = value


class _HttpClient:
	def __init__(self) -> None:
		self.cookies = _CookieJar({})


class _Response:
	def __init__(self) -> None:
		self.cookies = _CookieJar({"keep": "value", "skip": ""})


def test_browser_headers_applies_auth_metadata() -> None:
	headers = browser_headers(
		{"Accept": "application/json"},
		{"user_agent": "UA", "client_id": "client-1"},
		include_client_id=True,
		default_platform="macOS",
	)

	assert headers["Accept"] == "application/json"
	assert headers["User-Agent"] == "UA"
	assert headers["x-zp-client-id"] == "client-1"
	assert headers["sec-ch-ua-platform"]


def test_browser_headers_derives_recruiter_zp_token_from_managed_bst_cookie() -> None:
	headers = browser_headers(
		{"Accept": "application/json"},
		{"cookies": {"bst": "managed-token"}},
		include_zp_token=True,
	)

	assert headers["zp_token"] == "managed-token"


def test_referer_header_uses_map_or_fallback() -> None:
	assert referer_header("/a", {"/a": "https://example.test/a"}, "https://example.test/") == {
		"Referer": "https://example.test/a"
	}
	assert referer_header("/b", {"/a": "https://example.test/a"}, "https://example.test/") == {
		"Referer": "https://example.test/"
	}


def test_merge_response_cookies_skips_empty_values() -> None:
	client = _HttpClient()

	merge_response_cookies(client, _Response())

	assert client.cookies.values == {"keep": "value"}


def test_add_stoken_to_get_params_only_changes_get_requests() -> None:
	get_kwargs = {"params": {"page": 1}}
	post_kwargs = {"data": {"page": 1}}

	add_stoken_to_get_params("GET", get_kwargs, "token-1")
	add_stoken_to_get_params("POST", post_kwargs, "token-1")

	assert get_kwargs["params"] == {"page": 1, "__zp_stoken__": "token-1"}
	assert post_kwargs == {"data": {"page": 1}}
