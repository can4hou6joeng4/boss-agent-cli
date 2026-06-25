"""Browser/CDP backed Zhilian recruiter automation session."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from patchright._impl._errors import Error as PatchrightError

from boss_agent_cli.automation.zhilian_selectors import (
	SelectorGroup,
	SelectorHealthReport,
	ZhilianRecruiterSelectors,
)


class LocatorLike(Protocol):
	def count(self) -> int: ...
	def nth(self, index: int) -> "LocatorLike": ...
	def inner_text(self, timeout: int | None = None) -> str: ...
	def get_attribute(self, name: str) -> str | None: ...
	def click(self, timeout: int | None = None) -> None: ...
	def fill(self, value: str, timeout: int | None = None) -> None: ...
	def is_visible(self, timeout: int | None = None) -> bool: ...


class PageLike(Protocol):
	url: str
	def title(self) -> str: ...
	def locator(self, selector: str) -> LocatorLike: ...
	def content(self) -> str: ...
	def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 15000) -> None: ...
	def wait_for_timeout(self, timeout: int) -> None: ...
	def screenshot(self, path: str, full_page: bool = True) -> None: ...


class ZhilianBrowserRecruiterSession:
	"""Recruiter automation session over a logged-in Zhilian browser page."""

	def __init__(
		self,
		page: PageLike,
		*,
		selectors: ZhilianRecruiterSelectors | None = None,
		diagnostics_dir: Path | None = None,
	) -> None:
		self._page = page
		self._selectors = selectors or ZhilianRecruiterSelectors()
		self._diagnostics_dir = diagnostics_dir
		self._refs: dict[str, int] = {}
		self._last_warning = ""

	def user_info(self) -> dict[str, Any]:
		report = self.health_report(require_scan=True, require_write=False)
		if report.ok:
			return {"code": 200, "data": {"url": report.url, "title": report.title}}
		return {"code": 500, "message": report.reason, "diagnostic": _report_dict(report)}

	def recruiter_conversations(self, tabs: list[str], max_per_tab: int) -> dict[str, Any]:
		report = self.health_report(require_scan=True, require_write=False)
		if not report.ok:
			return {"code": 500, "message": report.reason, "diagnostic": _report_dict(report)}
		locator = self._required_locator(self._selectors.conversation_item)
		items: list[dict[str, str]] = []
		for index in range(min(locator.count(), max_per_tab)):
			item = locator.nth(index)
			text = _safe_text(item)
			if not _actionable_session_text(text):
				continue
			ref_id = _ref_id(item, text)
			self._refs[ref_id] = index
			items.append(
				{
					"id": ref_id,
					"candidateName": _first_line(text) or ref_id,
					"lastMessage": text,
				}
			)
		return {"code": 200, "data": {"items": items[:max_per_tab]}}

	def recruiter_conversation(self, ref_id: str) -> dict[str, Any]:
		index = self._refs.get(ref_id)
		if index is not None:
			self._required_locator(self._selectors.conversation_item).nth(index).click(timeout=3000)
			self._page.wait_for_timeout(500)
		report = self.health_report(require_scan=False, require_messages=True, require_write=False)
		if not report.ok:
			return {"code": 500, "message": report.reason, "diagnostic": _report_dict(report)}
		locator = self._required_locator(self._selectors.message_item)
		items = [_message_row(locator.nth(index)) for index in range(locator.count())]
		return {"code": 200, "data": {"items": items}}

	def send_recruiter_message(self, ref_id: str, message: str) -> dict[str, Any]:
		selected = self._select_conversation(ref_id)
		if not selected:
			return _blocked_response(f"zhilian conversation ref not found: {ref_id}")
		self._page.wait_for_timeout(300)
		report = self.health_report(require_scan=False, require_write=True)
		if not report.ok:
			return {"code": 500, "message": report.reason, "diagnostic": _report_dict(report)}
		input_locator = self._visible_locator(self._selectors.message_input)
		if input_locator is None:
			return _blocked_response("zhilian selector health missing: message_input")
		try:
			input_locator.fill(message, timeout=3000)
			self._required_locator(self._selectors.send_button).nth(0).click(timeout=3000)
		except (PatchrightError, RuntimeError, TypeError) as exc:
			return _blocked_response(f"zhilian send action failed: {exc}")
		return {"code": 200, "data": {"id": ref_id}}

	def exchange_recruiter_contact(self, ref_id: str) -> dict[str, Any]:
		selected = self._select_conversation(ref_id)
		if not selected:
			return _blocked_response(f"zhilian conversation ref not found: {ref_id}")
		self._page.wait_for_timeout(300)
		report = self.health_report(require_scan=False, require_exchange=True)
		if not report.ok:
			return {"code": 500, "message": report.reason, "diagnostic": _report_dict(report)}
		try:
			self._required_locator(self._selectors.exchange_button).nth(0).click(timeout=3000)
		except (PatchrightError, RuntimeError, TypeError) as exc:
			return _blocked_response(f"zhilian exchange action failed: {exc}")
		return {"code": 200, "data": {"id": ref_id}}

	def detect_safety_warning(self) -> str | None:
		if self._last_warning:
			return self._last_warning
		text = _safe_content(self._page)
		for keyword in self._selectors.safety_keywords:
			if keyword in text:
				self._last_warning = f"zhilian platform verification required: {keyword}"
				return self._last_warning
		return None

	def health_report(
		self,
		*,
		require_scan: bool = False,
		require_messages: bool = False,
		require_write: bool = False,
		require_exchange: bool = False,
	) -> SelectorHealthReport:
		warning = self.detect_safety_warning()
		if warning:
			return self._report(False, warning)
		missing = []
		for group in _required_groups(
			self._selectors,
			require_scan=require_scan,
			require_messages=require_messages,
			require_write=require_write,
			require_exchange=require_exchange,
		):
			if group.name == self._selectors.message_input.name:
				count = self._visible_count(group)
			else:
				count = self._count(group)
			if count == 0:
				missing.append(group.name)
		if missing:
			return self._report(False, f"zhilian selector health missing: {', '.join(missing)}", tuple(missing))
		return self._report(True, "ok")

	def _select_conversation(self, ref_id: str) -> bool:
		index = self._refs.get(ref_id)
		if index is None:
			index = self._find_conversation_index(ref_id)
		if index is None:
			return False
		self._required_locator(self._selectors.conversation_item).nth(index).click(timeout=3000)
		self._refs[ref_id] = index
		return True

	def _find_conversation_index(self, ref_id: str) -> int | None:
		locator = self._required_locator(self._selectors.conversation_item)
		for index in range(locator.count()):
			item = locator.nth(index)
			text = _safe_text(item)
			if _ref_id(item, text) == ref_id:
				return index
		return None

	def _required_locator(self, group: SelectorGroup) -> LocatorLike:
		for selector in group.selectors:
			locator = self._page.locator(selector)
			if locator.count() > 0:
				return locator
		return self._page.locator(group.selectors[0])

	def _count(self, group: SelectorGroup) -> int:
		for selector in group.selectors:
			count = self._page.locator(selector).count()
			if count > 0:
				return count
		return 0

	def _visible_count(self, group: SelectorGroup) -> int:
		return 1 if self._visible_locator(group) is not None else 0

	def _visible_locator(self, group: SelectorGroup) -> LocatorLike | None:
		for selector in group.selectors:
			locator = self._page.locator(selector)
			for index in range(locator.count()):
				candidate = locator.nth(index)
				if _safe_visible(candidate):
					return candidate
		return None

	def _report(
		self,
		ok: bool,
		reason: str,
		missing: tuple[str, ...] = (),
	) -> SelectorHealthReport:
		return SelectorHealthReport(
			ok=ok,
			reason=reason,
			missing=missing,
			url=getattr(self._page, "url", ""),
			title=_safe_title(self._page),
			screenshot_path=self._diagnostic_screenshot() if not ok else "",
		)

	def _diagnostic_screenshot(self) -> str:
		if self._diagnostics_dir is None:
			return ""
		self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
		path = self._diagnostics_dir / "zhilian-selector-health.png"
		try:
			self._page.screenshot(path=str(path), full_page=True)
		except (RuntimeError, OSError, TypeError):
			return ""
		return str(path)


def _required_groups(
	selectors: ZhilianRecruiterSelectors,
	*,
	require_scan: bool,
	require_messages: bool,
	require_write: bool,
	require_exchange: bool,
) -> tuple[SelectorGroup, ...]:
	groups: list[SelectorGroup] = []
	if require_scan:
		groups.append(selectors.conversation_item)
	if require_messages:
		groups.append(selectors.message_item)
	if require_write:
		groups.extend((selectors.message_input, selectors.send_button))
	if require_exchange:
		groups.append(selectors.exchange_button)
	return tuple(groups)


def _safe_title(page: PageLike) -> str:
	try:
		return page.title()
	except (RuntimeError, TypeError):
		return ""


def _safe_content(page: PageLike) -> str:
	try:
		return page.content()
	except (RuntimeError, TypeError):
		return ""


def _safe_text(locator: LocatorLike) -> str:
	try:
		return locator.inner_text(timeout=1000).strip()
	except (RuntimeError, TypeError):
		return ""


def _safe_visible(locator: LocatorLike) -> bool:
	try:
		return locator.is_visible(timeout=500)
	except (PatchrightError, RuntimeError, TypeError):
		return False


def _first_line(text: str) -> str:
	return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _actionable_session_text(text: str) -> bool:
	blocked_markers = (
		"智联小秘书",
		"不合适",
		"暂时不考虑",
		"不考虑这个机会",
		"已删除",
	)
	return bool(text.strip()) and not any(marker in text for marker in blocked_markers)


def _ref_id(locator: LocatorLike, text: str) -> str:
	for attr in ("data-id", "data-conversation-id", "data-candidate-id", "href"):
		value = locator.get_attribute(attr)
		if value:
			return value
	return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _message_row(locator: LocatorLike) -> dict[str, str]:
	text = _safe_text(locator)
	class_name = locator.get_attribute("class") or ""
	direction = "outgoing" if any(token in class_name.lower() for token in ("self", "mine", "right", "out")) else "incoming"
	return {"from": direction, "content": text}


def _report_dict(report: SelectorHealthReport) -> dict[str, Any]:
	return {
		"ok": report.ok,
		"reason": report.reason,
		"missing": list(report.missing),
		"url": report.url,
		"title": report.title,
		"screenshot_path": report.screenshot_path,
	}


def _blocked_response(reason: str) -> dict[str, Any]:
	return {"code": 500, "message": reason}
