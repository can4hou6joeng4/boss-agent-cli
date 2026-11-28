"""CDP 刷新必须遵守来源策略，不能绕过 may_create_context（Issue #3145）。"""

from pathlib import Path

import pytest

from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, POLICIES
from boss_agent_cli.auth import browser as browser_module
from boss_agent_cli.auth import manager as manager_module
from boss_agent_cli.auth.manager import AuthManager


class FakePage:
	def __init__(self, url: str = "https://www.zhipin.com/") -> None:
		self.url = url

	def close(self) -> None:
		return None


class FakeContext:
	def __init__(self, pages: list[FakePage] | None = None) -> None:
		self.pages = pages or []
		self.created_pages: list[FakePage] = []

	def new_page(self) -> FakePage:
		page = FakePage()
		self.created_pages.append(page)
		return page

	def cookies(self) -> list[dict[str, str]]:
		return []


class FakeBrowser:
	def __init__(self, contexts: list[FakeContext]) -> None:
		self.contexts = contexts
		self.new_context_calls = 0

	def new_context(self) -> FakeContext:
		self.new_context_calls += 1
		return FakeContext()


class FakePlaywright:
	def __init__(self, browser: FakeBrowser) -> None:
		self._browser = browser
		self.stopped = False

	class _Chromium:
		def __init__(self, outer: "FakePlaywright") -> None:
			self._outer = outer

		def connect_over_cdp(self, ws_url: str) -> FakeBrowser:
			return self._outer._browser

	chromium: "FakePlaywright._Chromium"

	def stop(self) -> None:
		self.stopped = True


class FakeStarter:
	def __init__(self, playwright: FakePlaywright) -> None:
		self._playwright = playwright

	def start(self) -> FakePlaywright:
		return self._playwright


@pytest.fixture
def fake_cdp(monkeypatch: pytest.MonkeyPatch):
	def build(contexts: list[FakeContext]) -> tuple[FakeBrowser, FakePlaywright]:
		browser = FakeBrowser(contexts)
		playwright = FakePlaywright(browser)
		playwright.chromium = FakePlaywright._Chromium(playwright)
		monkeypatch.setattr(browser_module, "sync_playwright", lambda: FakeStarter(playwright))
		monkeypatch.setattr(browser_module, "probe_cdp", lambda url: "ws://fake-cdp")
		monkeypatch.setattr(browser_module, "_warm_home_for_runtime", lambda *a, **k: True)
		monkeypatch.setattr(browser_module, "_extract_stoken", lambda *a, **k: "fresh-stoken")
		monkeypatch.setattr(browser_module, "_raise_if_security_page", lambda *a, **k: None)
		monkeypatch.setattr(browser_module.time, "sleep", lambda *_: None)
		return browser, playwright

	return build


def test_stored_cookie_refuses_to_create_anonymous_cdp_context(fake_cdp) -> None:
	browser, playwright = fake_cdp([])

	with pytest.raises(BrowserSourceUnavailable) as caught:
		browser_module.refresh_stoken_via_cdp(
			"http://127.0.0.1:9222", policy=POLICIES["stored-cookie"]
		)

	assert caught.value.code == "CDP_UNAVAILABLE"
	assert browser.new_context_calls == 0
	assert playwright.stopped is True


def test_existing_browser_refuses_to_create_anonymous_cdp_context(fake_cdp) -> None:
	browser, _ = fake_cdp([])

	with pytest.raises(BrowserSourceUnavailable) as caught:
		browser_module.refresh_stoken_via_cdp(
			"http://127.0.0.1:9222", policy=POLICIES["existing-browser"]
		)

	assert caught.value.code == "BROWSER_SESSION_NOT_FOUND"
	assert browser.new_context_calls == 0


def test_auto_policy_still_creates_a_context_when_none_exists(fake_cdp) -> None:
	browser, _ = fake_cdp([])

	token = browser_module.refresh_stoken_via_cdp("http://127.0.0.1:9222", policy=POLICIES["auto"])

	assert token == "fresh-stoken"
	assert browser.new_context_calls == 1


def test_stored_cookie_still_refreshes_in_an_existing_context(fake_cdp) -> None:
	browser, _ = fake_cdp([FakeContext()])

	token = browser_module.refresh_stoken_via_cdp(
		"http://127.0.0.1:9222", policy=POLICIES["stored-cookie"]
	)

	assert token == "fresh-stoken"
	assert browser.new_context_calls == 0
	assert len(browser.contexts[0].created_pages) == 1


def test_force_refresh_passes_the_source_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	seen: dict[str, object] = {}
	token = {
		"cookies": {"wt2": "synthetic"},
		"stoken": "old-stoken",
		"user_agent": "synthetic-UA",
	}
	manager = AuthManager(tmp_path)
	manager._store.save(token)

	def fake_refresh(cdp_url=None, *, policy=None):
		seen["cdp_url"] = cdp_url
		seen["policy"] = policy
		return "fresh-stoken"

	monkeypatch.setattr(manager_module, "probe_cdp", lambda url: "ws://fake-cdp")
	monkeypatch.setattr(manager_module, "refresh_stoken_via_cdp", fake_refresh)

	manager.force_refresh(cdp_url="http://127.0.0.1:9222", browser_source="stored-cookie")

	assert seen["cdp_url"] == "http://127.0.0.1:9222"
	assert getattr(seen["policy"], "name", None) == "stored-cookie"
	assert manager._store.load()["stoken"] == "fresh-stoken"
