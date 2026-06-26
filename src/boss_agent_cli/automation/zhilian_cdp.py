"""CDP connection helpers for Zhilian recruiter browser automation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent_cli.auth.browser import _DEFAULT_CDP_URL, _is_zhilian_url, probe_cdp
from boss_agent_cli.automation.zhilian_browser import (
	PageLike,
	ZhilianBrowserRecruiterSession,
)
from boss_agent_cli.automation.zhilian_selectors import ZhilianRecruiterSelectors


def create_zhilian_browser_session_from_cdp(
	*,
	cdp_url: str | None,
	diagnostics_dir: Path | None,
) -> ZhilianBrowserRecruiterSession:
	from patchright.sync_api import sync_playwright

	endpoint = cdp_url or _DEFAULT_CDP_URL
	ws_url = probe_cdp(endpoint) or endpoint
	pw = sync_playwright().start()
	try:
		browser = pw.chromium.connect_over_cdp(ws_url)
	except Exception as exc:
		pw.stop()
		raise RuntimeError(f"cannot connect to CDP Chrome at {endpoint}") from exc
	if not browser.contexts:
		pw.stop()
		raise RuntimeError("CDP Chrome has no browser context")
	context = browser.contexts[0]
	page = _find_zhilian_page(context.pages)
	if page is None:
		page = context.new_page()
		page.goto(
			ZhilianRecruiterSelectors().chat_urls[0],
			wait_until="domcontentloaded",
			timeout=15000,
		)
	return ZhilianBrowserRecruiterSession(_as_page_like(page), diagnostics_dir=diagnostics_dir)


def _find_zhilian_page(pages: list[Any]) -> Any | None:
	for page in pages:
		url = getattr(page, "url", "")
		if _is_zhilian_url(url) and _is_zhilian_chat_url(url):
			return page
	for page in pages:
		url = getattr(page, "url", "")
		if _is_zhilian_url(url) and any(token in url for token in ("im", "chat")):
			return page
	for page in pages:
		if _is_zhilian_url(getattr(page, "url", "")):
			return page
	return None


def _is_zhilian_chat_url(url: str) -> bool:
	return any(path in url for path in ("/app/im", "/im", "/chat"))


def _as_page_like(page: Any) -> PageLike:
	return page
