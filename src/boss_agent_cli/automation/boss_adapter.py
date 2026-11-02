"""BOSS recruiter automation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.zhipin_errors import raise_platform_risk_response, response_message
from boss_agent_cli.display import risk_error_message

from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	PlatformAction,
	PlatformHealth,
)


class BossRecruiterPlatformLike(Protocol):
	def friend_list(
		self,
		page: int = 1,
		label_id: int = 0,
		job_id: str | None = None,
	) -> dict[str, Any]: ...
	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]: ...
	def chat_history(
		self,
		gid: int,
		*,
		count: int = 20,
		max_msg_id: int | None = None,
	) -> dict[str, Any]: ...
	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]: ...
	def exchange_request_by_friend(
		self,
		friend_id: int,
		exchange_type: int,
	) -> dict[str, Any]: ...


class BossRecruiterAutomationPlatform:
	"""Automation adapter over the existing BOSS recruiter platform."""

	name = "zhipin"

	def __init__(self, recruiter: BossRecruiterPlatformLike | None = None) -> None:
		self._recruiter = recruiter
		self._warning = ""
		self._risk: PlatformRiskError | None = None

	@classmethod
	def from_context(
		cls,
		*,
		data_dir: Path | None,
		delay: tuple[float, float],
		cdp_url: str | None,
		live: bool,
	) -> "BossRecruiterAutomationPlatform":
		if not live or data_dir is None:
			return cls()
		from boss_agent_cli.api.recruiter_client import BossRecruiterClient
		from boss_agent_cli.auth.manager import AuthManager
		from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

		auth = AuthManager(data_dir, platform="zhipin")
		client = BossRecruiterClient(auth, delay=delay, cdp_url=cdp_url)
		return cls(BossRecruiterPlatform(client))

	def ensure_session(self) -> None:
		if self._recruiter is None:
			self._warning = "zhipin recruiter live adapter is not attached"

	def scan_conversations(
		self,
		tabs: list[str],
		max_per_tab: int,
	) -> list[ConversationRef]:
		recruiter = self._recruiter
		if recruiter is None:
			return [_diagnostic_ref("zhipin", self._warning)]
		self._ensure_not_risk_stopped()
		items: list[dict[str, Any]] = []
		if "新招呼" in tabs:
			items.extend(_items_from_response(self._checked_response(lambda: recruiter.greet_list(page=1))))
		if "未读" in tabs or not items:
			items.extend(_items_from_response(self._checked_response(lambda: recruiter.friend_list(page=1))))
		return [_ref_from_item(item) for item in items[:max_per_tab]]

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		self._ensure_not_risk_stopped()
		if ref.conversation is not None:
			return ref.conversation
		recruiter = self._recruiter
		if recruiter is None:
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		try:
			gid = int(ref.id)
		except ValueError:
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		return _conversation_from_history(ref.id, self._checked_response(lambda: recruiter.chat_history(gid)))

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		recruiter = self._recruiter
		if recruiter is None:
			return ActionResult("blocked", {"reason": self._warning})
		self._ensure_not_risk_stopped()
		friend_id = _friend_id(ref.id)
		match action:
			case PlatformAction.SEND_QUESTIONNAIRE | PlatformAction.SEND_FOLLOW_UP:
				result = self._checked_response(
					lambda: recruiter.send_message_by_friend(friend_id, message), require_success=False
				)
			case PlatformAction.EXCHANGE_CONTACT:
				result = self._checked_response(
					lambda: recruiter.exchange_request_by_friend(friend_id, 2), require_success=False
				)
			case PlatformAction.CREATE_INTERVIEW_LEAD:
				return ActionResult("executed", {"local_only": True})
			case _:
				return ActionResult("blocked", {"reason": f"unsupported action {action}"})
		return _action_result(result)

	def _ensure_not_risk_stopped(self) -> None:
		if self._risk is not None:
			raise self._risk

	def _checked_response(
		self, operation: Callable[[], dict[str, Any]], *, require_success: bool = True
	) -> dict[str, Any]:
		self._ensure_not_risk_stopped()
		try:
			response = operation()
			raise_platform_risk_response(response)
		except PlatformRiskError as exc:
			# 风控消息来自平台响应原文，可能带临时凭据；只保留登记表文案。
			self._warning = risk_error_message(exc.code)
			self._risk = exc
			raise
		if require_success and not (response.get("code") == 0 or response.get("ok") is True):
			raise RuntimeError(response_message(response) or "招聘者读取失败，不能将错误响应视为空列表")
		return response

	def health_check(self) -> PlatformHealth:
		if self._warning:
			return PlatformHealth(status="needs-live-session", warning=self._warning)
		return PlatformHealth(status="healthy", checks=({"name": "zhipin", "ok": True},))

	def detect_safety_warning(self) -> str | None:
		return self._warning or None


def _diagnostic_ref(platform: str, warning: str) -> ConversationRef:
	return ConversationRef(
		id=f"{platform}-diagnostic",
		tab="diagnostic",
		diagnostic=warning or "platform adapter unavailable",
	)


def _items_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
	data = response.get("zpData") or response.get("data") or response
	if isinstance(data, dict):
		for key in ("list", "result", "items", "geekList", "friendList"):
			value = data.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]
	return []


def _ref_from_item(item: dict[str, Any]) -> ConversationRef:
	raw_id = item.get("friendId") or item.get("gid") or item.get("id") or item.get("uid")
	title = str(item.get("name") or item.get("geekName") or raw_id or "candidate")
	return ConversationRef(id=str(raw_id or title), tab="scan", reason=title)


def _conversation_from_history(ref_id: str, response: dict[str, Any]) -> Conversation:
	items = _items_from_response(response)
	ordered = tuple(
		(
			"outgoing" if item.get("from") in {"boss", "self", 1} else "incoming",
			str(item.get("content") or item.get("text") or ""),
		)
		for item in items
	)
	incoming = tuple(text for direction, text in ordered if direction == "incoming")
	outgoing = tuple(text for direction, text in ordered if direction == "outgoing")
	all_messages = tuple(text for _, text in ordered)
	return Conversation(
		title=ref_id,
		incoming_messages=incoming,
		outgoing_messages=outgoing,
		ordered_messages=ordered,
		all_messages=all_messages,
		fingerprint=ConversationFingerprint(ref_id),
	)


def _friend_id(value: str) -> int:
	try:
		return int(value)
	except ValueError:
		return 0


def _action_result(response: dict[str, Any]) -> ActionResult:
	code = response.get("code")
	if code == 0 or response.get("ok") is True:
		return ActionResult("executed", {"response": response})
	if response.get("__cli_error_code__") == "ACTION_UNCONFIRMED":
		# 页面动作可能已经生效：这不是「确定没发出」的普通失败，
		# 标成 blocked 会让待执行队列在下一轮自动重发。
		return ActionResult("unconfirmed", {"reason": "ACTION_UNCONFIRMED"})
	return ActionResult("blocked", {"reason": str(response.get("message", code))})
