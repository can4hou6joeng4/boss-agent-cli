"""Explicit operating-mode policy for crawl research capabilities."""

from __future__ import annotations

ASSISTED_MODE = "assisted"
RESEARCH_MODE = "research"

RESEARCH_CAPABILITIES = {
	"crawl": {"risk": "platform-data-collection", "data": "job-list-and-job-detail"},
	"cdp": {"risk": "browser-debug-protocol", "data": "browser-session-metadata"},
	"hook": {"risk": "page-script-injection", "data": "user-provided-script-content"},
}


def require_research(research: bool, *, hook_profile: str) -> str:
	"""Return the active mode or reject browser collection without explicit consent."""
	if not research:
		raise ValueError("crawl、CDP 和 Hook 仅可在显式 --research 模式运行")
	if hook_profile != "none" and not research:
		raise ValueError("Hook 注入仅可在显式 --research 模式运行")
	return RESEARCH_MODE
