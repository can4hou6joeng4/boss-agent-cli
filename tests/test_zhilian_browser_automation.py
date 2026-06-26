"""Zhilian recruiter browser automation tests."""

from __future__ import annotations

from pathlib import Path

from boss_agent_cli.automation.models import PlatformAction
from boss_agent_cli.automation.zhilian_cdp import _find_zhilian_page
from boss_agent_cli.automation.zhilian_browser import ZhilianBrowserRecruiterSession
from boss_agent_cli.automation.zhilian_browser_actions import (
	execute_browser_action,
)


class FakeLocator:
	def __init__(self, items: list[dict[str, str]], page: "FakePage") -> None:
		self._items = items
		self._page = page
		self._index = 0

	def count(self) -> int:
		return len(self._items)

	def nth(self, index: int) -> "FakeLocator":
		child = FakeLocator(self._items, self._page)
		child._index = index
		return child

	def inner_text(self, timeout: int | None = None) -> str:
		return self._item().get("text", "")

	def get_attribute(self, name: str) -> str | None:
		return self._item().get(name)

	def click(self, timeout: int | None = None) -> None:
		self._page.clicked.append(self._item().get("id", self._item().get("text", "")))

	def fill(self, value: str, timeout: int | None = None) -> None:
		self._page.filled.append(value)

	def is_visible(self, timeout: int | None = None) -> bool:
		return self._item().get("visible", "true") != "false"

	def _item(self) -> dict[str, str]:
		return self._items[self._index]


class FakePage:
	def __init__(self, *, missing: tuple[str, ...] = (), content: str = "") -> None:
		self.url = "https://rd.zhaopin.com/im"
		self.clicked: list[str] = []
		self.filled: list[str] = []
		self.screenshots: list[str] = []
		self._content = content
		self._selectors = {
			"[data-zp-automation='conversation-item']": [
				{"id": "zl-101", "data-id": "zl-101", "text": "智联高分\n上海 3年销售 大专 想看机会"},
			],
			"[data-zp-automation='message-item']": [
				{"class": "message incoming", "text": "你好，我在上海做过3年销售，擅长客户沟通，大专，想看机会"},
			],
			"[data-zp-automation='message-input']": [{"id": "input", "text": ""}],
			"[data-zp-automation='send-message']": [{"id": "send", "text": "发送"}],
			"[data-zp-automation='exchange-contact']": [{"id": "exchange", "text": "交换微信"}],
		}
		for key in missing:
			self._selectors[key] = []

	def title(self) -> str:
		return "智联招聘者沟通"

	def locator(self, selector: str) -> FakeLocator:
		return FakeLocator(self._selectors.get(selector, []), self)

	def content(self) -> str:
		return self._content

	def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 15000) -> None:
		self.url = url

	def wait_for_timeout(self, timeout: int) -> None:
		return None

	def screenshot(self, path: str, full_page: bool = True) -> None:
		Path(path).parent.mkdir(parents=True, exist_ok=True)
		Path(path).write_text("fake", encoding="utf-8")
		self.screenshots.append(path)


class FakeCdpPage:
	def __init__(self, url: str) -> None:
		self.url = url


def test_zhilian_browser_session_scans_and_reads_conversation(tmp_path: Path) -> None:
	page = FakePage()
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)

	response = session.recruiter_conversations(["未读"], 1)
	conversation = session.recruiter_conversation("zl-101")

	assert response["code"] == 200
	assert response["data"]["items"][0]["id"] == "zl-101"
	assert conversation["data"]["items"][0]["from"] == "incoming"
	assert page.clicked == ["zl-101"]


def test_zhilian_browser_session_skips_system_and_rejected_sessions(tmp_path: Path) -> None:
	page = FakePage()
	page._selectors["[data-zp-automation='conversation-item']"] = [
		{"id": "system", "data-id": "system", "text": "智联小秘书\n职位满4周啦\n不合适"},
		{"id": "rejected", "data-id": "rejected", "text": "段女士\n很抱歉，我暂时不考虑这个机会\n不合适"},
		{"id": "zl-101", "data-id": "zl-101", "text": "智联高分\n上海 3年销售 大专 想看机会"},
	]
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)

	response = session.recruiter_conversations(["未读"], 10)

	items = response["data"]["items"]
	assert [item["id"] for item in items] == ["zl-101"]


def test_zhilian_cdp_prefers_chat_page_over_recommend_page() -> None:
	recommend = FakeCdpPage("https://rd6.zhaopin.com/app/recommend")
	chat = FakeCdpPage("https://rd6.zhaopin.com/app/im?sessionId=abc")

	selected = _find_zhilian_page([recommend, chat])

	assert selected is chat


def test_zhilian_cdp_rejects_embedded_zhaopin_hostname() -> None:
	fake_chat = FakeCdpPage("https://rd6.zhaopin.com.evil.example/app/im")
	fake_query = FakeCdpPage("https://evil.example/chat?next=https://rd6.zhaopin.com/app/im")
	valid_page = FakeCdpPage("https://rd6.zhaopin.com/profile")

	selected = _find_zhilian_page([fake_chat, fake_query, valid_page])

	assert selected is valid_page


def test_zhilian_cdp_ignores_invalid_zhaopin_like_url() -> None:
	selected = _find_zhilian_page([FakeCdpPage("not-a-url-with-zhaopin.com")])

	assert selected is None


def test_zhilian_browser_session_sends_message_after_selector_health(tmp_path: Path) -> None:
	page = FakePage()
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)
	session.recruiter_conversations(["未读"], 1)

	result = execute_browser_action(
		session,
		PlatformAction.SEND_QUESTIONNAIRE,
		"请问近期是否看机会？",
		"zl-101",
	)

	assert result.status == "executed"
	assert page.filled == ["请问近期是否看机会？"]
	assert "send" in page.clicked


def test_zhilian_browser_session_sends_after_matching_ref_without_prior_scan(tmp_path: Path) -> None:
	page = FakePage()
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)

	result = execute_browser_action(
		session,
		PlatformAction.SEND_QUESTIONNAIRE,
		"请问近期是否看机会？",
		"zl-101",
	)

	assert result.status == "executed"
	assert "zl-101" in page.clicked
	assert "send" in page.clicked


def test_zhilian_browser_session_blocks_when_ref_cannot_be_selected(tmp_path: Path) -> None:
	page = FakePage()
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)

	result = execute_browser_action(
		session,
		PlatformAction.SEND_QUESTIONNAIRE,
		"请问近期是否看机会？",
		"unknown-ref",
	)

	assert result.status == "blocked"
	assert "conversation ref not found" in result.details["reason"]
	assert page.filled == []


def test_zhilian_browser_session_blocks_when_input_is_hidden(tmp_path: Path) -> None:
	page = FakePage()
	page._selectors["[data-zp-automation='message-input']"] = [{"id": "input", "visible": "false"}]
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)
	session.recruiter_conversations(["未读"], 1)

	result = execute_browser_action(
		session,
		PlatformAction.SEND_QUESTIONNAIRE,
		"请问近期是否看机会？",
		"zl-101",
	)

	assert result.status == "blocked"
	assert "message_input" in result.details["reason"]
	assert page.filled == []


def test_zhilian_browser_session_exchanges_contact(tmp_path: Path) -> None:
	page = FakePage()
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)
	session.recruiter_conversations(["未读"], 1)

	result = execute_browser_action(session, PlatformAction.EXCHANGE_CONTACT, "", "zl-101")

	assert result.status == "executed"
	assert "exchange" in page.clicked


def test_zhilian_browser_session_blocks_when_send_selector_missing(tmp_path: Path) -> None:
	page = FakePage(missing=("[data-zp-automation='send-message']",))
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)
	session.recruiter_conversations(["未读"], 1)

	result = execute_browser_action(
		session,
		PlatformAction.SEND_FOLLOW_UP,
		"继续沟通",
		"zl-101",
	)

	assert result.status == "blocked"
	assert "send_button" in result.details["reason"]


def test_zhilian_browser_session_reports_platform_verification(tmp_path: Path) -> None:
	page = FakePage(content="请完成安全验证后继续")
	session = ZhilianBrowserRecruiterSession(page, diagnostics_dir=tmp_path)

	response = session.recruiter_conversations(["未读"], 1)

	assert response["code"] == 500
	assert "安全验证" in response["message"]
	assert page.screenshots
