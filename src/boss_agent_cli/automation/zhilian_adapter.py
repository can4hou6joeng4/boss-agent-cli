"""Zhilian recruiter automation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	PlatformAction,
	PlatformHealth,
)


class ZhilianSessionClientLike(Protocol):
	def user_info(self) -> dict[str, Any]: ...


class ZhilianRecruiterAutomationPlatform:
	"""Automation adapter for Zhilian recruiter-side conversations."""

	name = "zhilian"

	def __init__(
		self,
		client: ZhilianSessionClientLike | None = None,
		*,
		warning: str = "",
	) -> None:
		self._client = client
		self._warning = warning

	@classmethod
	def from_context(
		cls,
		*,
		data_dir: Path | None,
		delay: tuple[float, float],
		cdp_url: str | None,
		live: bool,
	) -> "ZhilianRecruiterAutomationPlatform":
		if not live or data_dir is None:
			return cls()
		from boss_agent_cli.automation.zhilian_cdp import (
			create_zhilian_browser_session_from_cdp,
		)

		try:
			session = create_zhilian_browser_session_from_cdp(
				cdp_url=cdp_url,
				diagnostics_dir=data_dir / "automation" / "selector-diagnostics",
			)
		except (ConnectionError, RuntimeError, OSError) as exc:
			return cls(warning=f"zhilian recruiter CDP browser session unavailable: {exc}")
		return cls(session)

	def ensure_session(self) -> None:
		if self._client is None:
			if not self._warning:
				self._warning = "zhilian recruiter live adapter is not attached"
			return
		try:
			response = self._client.user_info()
		except AttributeError:
			self._warning = "zhilian recruiter browser selectors are not implemented"
			return
		if response.get("code") not in {0, 200}:
			self._warning = str(response.get("message") or "zhilian recruiter selector health failed")

	def scan_conversations(
		self,
		tabs: list[str],
		max_per_tab: int,
	) -> list[ConversationRef]:
		if self._client is None:
			return [_diagnostic_ref(self._warning)]
		method = getattr(self._client, "recruiter_conversations", None)
		if not callable(method):
			return [_diagnostic_ref("zhilian recruiter selector health missing")]
		response = method(tabs, max_per_tab)
		if warning := _response_warning(response):
			return [_diagnostic_ref(warning)]
		return [_ref_from_item(item) for item in _items_from_response(response)]

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		if ref.conversation is not None:
			return ref.conversation
		if self._client is None:
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		method = getattr(self._client, "recruiter_conversation", None)
		if not callable(method):
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		response = method(ref.id)
		if _response_warning(response):
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		return _conversation_from_response(ref.id, response)

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		if self._client is None:
			return ActionResult("blocked", {"reason": self._warning})
		match action:
			case PlatformAction.SEND_QUESTIONNAIRE | PlatformAction.SEND_FOLLOW_UP:
				method = getattr(self._client, "send_recruiter_message", None)
				if not callable(method):
					return _missing_write_selector()
				try:
					result = method(ref.id, message)
				except (RuntimeError, OSError, TypeError) as exc:
					return _action_failed(exc)
			case PlatformAction.EXCHANGE_CONTACT:
				method = getattr(self._client, "exchange_recruiter_contact", None)
				if not callable(method):
					return _missing_write_selector()
				try:
					result = method(ref.id)
				except (RuntimeError, OSError, TypeError) as exc:
					return _action_failed(exc)
			case PlatformAction.CREATE_INTERVIEW_LEAD:
				return ActionResult("executed", {"local_only": True})
			case _:
				return ActionResult("blocked", {"reason": f"unsupported action {action}"})
		return _action_result(result)

	def health_check(self) -> PlatformHealth:
		if self._warning:
			return PlatformHealth(status="needs-selector-health", warning=self._warning)
		return PlatformHealth(status="healthy", checks=({"name": "zhilian", "ok": True},))

	def detect_safety_warning(self) -> str | None:
		if self._warning:
			if "CDP browser session unavailable" in self._warning:
				return None
			if "live adapter is not attached" in self._warning:
				return None
			return self._warning
		if self._client is None:
			return None
		method = getattr(self._client, "detect_safety_warning", None)
		if callable(method):
			return method()
		return None


def _diagnostic_ref(warning: str) -> ConversationRef:
	return ConversationRef(
		id="zhilian-diagnostic",
		tab="diagnostic",
		diagnostic=warning or "zhilian recruiter adapter unavailable",
	)


def _items_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
	data = response.get("data") or response.get("zpData") or response
	if isinstance(data, dict):
		for key in ("items", "list", "conversations", "rows"):
			value = data.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]
	return []


def _ref_from_item(item: dict[str, Any]) -> ConversationRef:
	raw_id = item.get("id") or item.get("conversationId") or item.get("candidateId")
	title = str(item.get("name") or item.get("candidateName") or raw_id or "candidate")
	message = str(item.get("lastMessage") or item.get("message") or "")
	return ConversationRef(id=str(raw_id or title), tab="scan", reason=message or title)


def _conversation_from_response(ref_id: str, response: dict[str, Any]) -> Conversation:
	items = _items_from_response(response)
	ordered = tuple(
		(
			"outgoing" if item.get("from") in {"recruiter", "self", 1} else "incoming",
			str(item.get("content") or item.get("text") or item.get("message") or ""),
		)
		for item in items
	)
	return Conversation(
		title=ref_id,
		incoming_messages=tuple(text for direction, text in ordered if direction == "incoming"),
		outgoing_messages=tuple(text for direction, text in ordered if direction == "outgoing"),
		ordered_messages=ordered,
		all_messages=tuple(text for _, text in ordered),
		fingerprint=ConversationFingerprint(ref_id),
	)


def _action_result(response: dict[str, Any]) -> ActionResult:
	code = response.get("code")
	if code in {0, 200} or response.get("ok") is True:
		return ActionResult("executed", {"response": response})
	return ActionResult("blocked", {"reason": str(response.get("message", code))})


def _response_warning(response: dict[str, Any]) -> str:
	code = response.get("code")
	if code in {0, 200} or response.get("ok") is True:
		return ""
	return str(response.get("message") or "zhilian recruiter selector health failed")


def _missing_write_selector() -> ActionResult:
	return ActionResult(
		"blocked",
		{"reason": "zhilian recruiter write selector is not implemented"},
	)


def _action_failed(exc: RuntimeError | OSError | TypeError) -> ActionResult:
	return ActionResult("blocked", {"reason": f"zhilian recruiter action failed: {exc}"})
