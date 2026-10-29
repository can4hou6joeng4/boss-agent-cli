from boss_agent_cli.api.httpx_helpers import (
	add_stoken_to_get_params,
	browser_headers,
	merge_response_cookies,
	referer_header,
	sec_ch_ua_brands,
	sec_ch_ua_platform,
)

_MAC_UA = (
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
_WIN_UA = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
_BRANDS = '"Chromium";v="145", "Not;A=Brand";v="99", "Google Chrome";v="145"'


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


def test_browser_headers_derives_platform_hint_from_user_agent() -> None:
	# The host OS must not override what the User-Agent claims.
	headers = browser_headers(
		{"Accept": "application/json", "User-Agent": _WIN_UA},
		{"user_agent": _MAC_UA},
	)

	assert headers["User-Agent"] == _MAC_UA
	assert headers["sec-ch-ua-platform"] == '"macOS"'


def test_browser_headers_platform_hint_matches_windows_user_agent() -> None:
	headers = browser_headers({"User-Agent": _WIN_UA}, {})

	assert headers["sec-ch-ua-platform"] == '"Windows"'
	assert headers["sec-ch-ua-mobile"] == "?0"


def test_browser_headers_keeps_base_hint_when_user_agent_is_unrecognized() -> None:
	headers = browser_headers(
		{"User-Agent": "UA", "sec-ch-ua-platform": '"macOS"'},
		{"user_agent": "UA"},
	)

	assert headers["sec-ch-ua-platform"] == '"macOS"'


def test_sec_ch_ua_platform_maps_mobile_and_chromeos_agents() -> None:
	assert sec_ch_ua_platform(_WIN_UA) == '"Windows"'
	assert sec_ch_ua_platform(_MAC_UA) == '"macOS"'
	assert sec_ch_ua_platform("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/156.0.0.0") == '"Android"'
	assert sec_ch_ua_platform("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/156.0.0.0") == '"Chrome OS"'
	assert sec_ch_ua_platform("Mozilla/5.0 (X11; Linux x86_64) Chrome/156.0.0.0") == '"Linux"'
	assert sec_ch_ua_platform("curl/8.0") is None


def test_sec_ch_ua_brands_follow_user_agent_version_and_keep_greasing() -> None:
	brands = sec_ch_ua_brands(_MAC_UA, _BRANDS)

	assert brands == '"Chromium";v="156", "Not;A=Brand";v="99", "Google Chrome";v="156"'


def test_sec_ch_ua_brands_unchanged_without_version_signal() -> None:
	assert sec_ch_ua_brands("UA", _BRANDS) == _BRANDS
	assert sec_ch_ua_brands(_MAC_UA, "") == ""


def test_browser_headers_marks_mobile_user_agent() -> None:
	headers = browser_headers(
		{"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36"},
		{},
	)

	assert headers["sec-ch-ua-mobile"] == "?1"
	assert headers["sec-ch-ua-platform"] == '"Android"'


def test_browser_headers_does_not_add_recruiter_token_globally() -> None:
	headers = browser_headers(
		{"Accept": "application/json"},
		{"cookies": {"bst": "managed-token"}},
	)

	assert "zp_token" not in headers


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


def test_browser_headers_replays_page_identity_headers() -> None:
	headers = browser_headers(
		{"User-Agent": _MAC_UA},
		{"token": "test-token", "zp_token": "abc~|def~"},
	)

	assert headers["token"] == "test-token"
	assert headers["zp_token"] == "abc~|def~"


def test_browser_headers_omits_page_identity_headers_when_absent() -> None:
	headers = browser_headers({"User-Agent": _MAC_UA}, {})

	assert "token" not in headers
	assert "zp_token" not in headers
