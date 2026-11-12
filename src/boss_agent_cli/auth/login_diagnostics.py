"""Bounded, local metadata for login risk errors; never inspect credentials or bodies."""

from typing import Any
from urllib.parse import urlsplit

from boss_agent_cli.api.browser_urls import is_zhipin_url


_STAGES = frozenset({
	"open_login", "wait_for_scan", "post_scan_wait", "read_user_agent",
	"warm_home", "read_credentials", "probe_session",
})
_TOP_PATHS = frozenset({
	"/", "/web/user/", "/web/geek/jobs", "/web/geek/recommend", "/web/geek/chat",
	"/web/passport/zp/security.html", "/web/passport/zp/verify.html", "/web/passport/zp/403.html",
})
_RESOURCE_TYPES = frozenset({
	"document", "stylesheet", "image", "media", "font", "script", "texttrack",
	"xhr", "fetch", "eventsource", "websocket", "manifest", "other",
})


def _top_path(page: Any) -> str:
	try:
		url = page.url
		if not isinstance(url, str):
			return "<unavailable>"
		path = urlsplit(url).path
		return path if is_zhipin_url(url) and path in _TOP_PATHS else "<other-or-unavailable>"
	except Exception:
		return "<unavailable>"


def login_diagnostic(page: Any, *, stage: str, response: Any = None) -> dict[str, object]:
	"""Only read cached event properties; missing metadata must not mask a risk error.

	Paths, stages and resource types use fixed vocabularies. No query strings,
	frame URLs, headers, response bodies, cookies or exception text are returned.
	"""
	diagnostic: dict[str, object] = {
		"origin": "response" if response is not None else ("session_probe" if stage == "probe_session" else "page_check"),
		"stage": stage if isinstance(stage, str) and stage in _STAGES else "unknown",
		"top_path": _top_path(page),
	}
	try:
		pages = page.context.pages
		if isinstance(pages, (list, tuple)):
			diagnostic["open_pages"] = len(pages)
	except Exception:
		pass
	if response is None:
		return diagnostic
	try:
		status = response.status
		if type(status) is int and 100 <= status <= 599:
			diagnostic["status"] = status
	except Exception:
		pass
	try:
		request = response.request
		resource_type = request.resource_type
		diagnostic["resource_type"] = (
			resource_type if isinstance(resource_type, str) and resource_type in _RESOURCE_TYPES else "unknown"
		)
		is_navigation = request.is_navigation_request()
		if isinstance(is_navigation, bool):
			diagnostic["is_navigation_request"] = is_navigation
	except Exception:
		diagnostic.setdefault("resource_type", "unknown")
	try:
		frame = response.frame
		if frame is None:
			scope = "unknown"
		elif frame is page.main_frame:
			scope = "main_frame"
		elif frame.page is page:
			scope = "child_frame"
		else:
			scope = "other_page"
		diagnostic["frame_scope"] = scope
	except Exception:
		diagnostic["frame_scope"] = "unknown"
	return diagnostic
