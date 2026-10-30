from boss_agent_cli.auth.page_identity import PageIdentityCapture, extract_page_identity


class _Request:
	def __init__(self, url: str, headers: dict[str, str]) -> None:
		self.url = url
		self.headers = headers


class _FakePage:
	def __init__(self) -> None:
		self.handlers: dict[str, list] = {}
		self.removed: list[str] = []

	def on(self, event: str, handler) -> None:
		self.handlers.setdefault(event, []).append(handler)

	def remove_listener(self, event: str, handler) -> None:
		self.handlers[event].remove(handler)
		self.removed.append(event)

	def emit(self, event: str, payload) -> None:
		for handler in list(self.handlers.get(event, [])):
			handler(payload)


def test_extract_page_identity_is_case_insensitive() -> None:
	assert extract_page_identity({"Token": "abc", "ZP_TOKEN": "x~|y~"}) == {
		"token": "abc",
		"zp_token": "x~|y~",
	}


def test_extract_page_identity_ignores_unrelated_and_empty_values() -> None:
	assert extract_page_identity({"token": "", "referer": "https://x", "traceid": "F-1"}) == {}
	assert extract_page_identity({}) == {}


def test_capture_only_records_business_requests() -> None:
	page = _FakePage()
	capture = PageIdentityCapture(page)

	page.emit("request", _Request("https://www.zhipin.com/static/app.js", {"token": "static"}))
	assert capture.captured == {}

	page.emit("request", _Request("https://www.zhipin.com/wapi/x.json", {"token": "real", "zp_token": "a~|b~"}))
	assert capture.captured == {"token": "real", "zp_token": "a~|b~"}


def test_capture_merges_headers_across_requests_until_complete() -> None:
	page = _FakePage()
	capture = PageIdentityCapture(page)

	# 早期请求可能只带 token；zp_token 在后续请求上才出现。
	page.emit("request", _Request("https://www.zhipin.com/wapi/a.json", {"token": "first"}))
	assert capture.captured == {"token": "first"}

	page.emit("request", _Request("https://www.zhipin.com/wapi/b.json", {"zp_token": "a~|b~"}))
	assert capture.captured == {"token": "first", "zp_token": "a~|b~"}

	assert capture.result() == {"token": "first", "zp_token": "a~|b~"}
	assert page.removed == ["request"]
	assert page.handlers["request"] == []


def test_capture_stops_collecting_once_complete() -> None:
	page = _FakePage()
	capture = PageIdentityCapture(page)

	page.emit("request", _Request("https://www.zhipin.com/wapi/a.json", {"token": "first", "zp_token": "a~|b~"}))
	page.emit("request", _Request("https://www.zhipin.com/wapi/b.json", {"token": "second"}))

	assert capture.captured == {"token": "first", "zp_token": "a~|b~"}


def test_capture_without_business_request_yields_empty_mapping() -> None:
	page = _FakePage()
	capture = PageIdentityCapture(page)

	assert capture.result() == {}
	assert capture.close() is None
