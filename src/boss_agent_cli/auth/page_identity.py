"""Capture the request identity a live BOSS page attaches to its own XHRs.

The page sets ``token`` and ``zp_token`` on every business request it makes
(captured evidence: ``docs/research/zhipin-request-identity-2026-10-29.md``).
Both values are generated in-page and stay stable for the lifetime of a
session, which means the httpx channel has to replay exactly the values the
browser produced — recomputing them is not possible from Python, and inventing
look-alikes would be a guess rather than a replay.

The listener has to be attached *before* the navigation that boots the SPA,
because the bootstrap requests are what carry the values.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

# Headers the page adds that the httpx channel must reproduce for the same
# cookie jar to look like one client.
CAPTURED_HEADERS: tuple[str, ...] = ("token", "zp_token")

_WAPI_MARKER = "/wapi/"


def extract_page_identity(headers: dict[str, str] | Any) -> dict[str, str]:
	"""Return the replayable subset of ``headers``.

	Header lookup is case-insensitive because CDP lowercases names while other
	producers do not.
	"""
	lowered = {str(k).lower(): v for k, v in dict(headers or {}).items()}
	found: dict[str, str] = {}
	for name in CAPTURED_HEADERS:
		value = lowered.get(name)
		if value:
			found[name] = str(value)
	return found


class PageIdentityCapture:
	"""Collect page identity headers from any ``/wapi/`` request on ``page``.

	Attach one before navigating; call :meth:`result` afterwards. A page that
	never issues a business request simply yields an empty mapping, which leaves
	the stored session without these headers rather than fabricating them.
	"""

	def __init__(self, page: Any) -> None:
		self._page = page
		self._captured: dict[str, str] = {}
		self._attached = False
		self._attach()

	def _attach(self) -> None:
		with suppress(Exception):
			self._page.on("request", self._on_request)
			self._attached = True

	def _on_request(self, request: Any) -> None:
		if self._complete:
			return
		url = getattr(request, "url", "") or ""
		if _WAPI_MARKER not in url:
			return
		with suppress(Exception):
			self._captured.update(extract_page_identity(getattr(request, "headers", {}) or {}))

	@property
	def _complete(self) -> bool:
		return all(name in self._captured for name in CAPTURED_HEADERS)

	@property
	def captured(self) -> dict[str, str]:
		return dict(self._captured)

	def wait_for_identity(self, timeout_ms: int = 0) -> dict[str, str]:
		"""Give the page up to ``timeout_ms`` to issue a business request.

		A reused tab has already finished booting, so the only way to observe its
		identity is to let the loop run and hope one of its periodic ``/wapi/``
		calls arrives. Returning empty is a valid outcome: the stored session then
		simply carries no page identity headers instead of invented ones.
		"""
		deadline = timeout_ms
		while not self._complete and deadline > 0:
			step = min(250, deadline)
			with suppress(Exception):
				self._page.wait_for_timeout(step)
			deadline -= step
		return self.captured

	def result(self) -> dict[str, str]:
		self.close()
		return self.captured

	def close(self) -> None:
		if not self._attached:
			return
		self._attached = False
		with suppress(Exception):
			self._page.remove_listener("request", self._on_request)
