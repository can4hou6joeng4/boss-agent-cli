"""BOSS recruiter automation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.models import safe_text
from boss_agent_cli.api.recruiter_resume import message_texts
from boss_agent_cli.api.zhipin_errors import (
	is_platform_success_code,
	raise_platform_risk_response,
	response_message,
)
from boss_agent_cli.display import risk_error_message

from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	PlatformAction,
	PlatformHealth,
)


_INVALID_FRIEND_ID = "会话缺少有效的正整数 friend_id，未访问平台"


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

	def __init__(
		self,
		recruiter: BossRecruiterPlatformLike | None = None,
		*,
		check_local_risk: Callable[[], None] | None = None,
	) -> None:
		self._recruiter = recruiter
		self._check_local_risk = check_local_risk
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
		return cls(BossRecruiterPlatform(client), check_local_risk=client.check_local_risk)

	def ensure_session(self) -> None:
		if self._recruiter is None:
			self._warning = "zhipin recruiter live adapter is not attached"
		elif self._check_local_risk is not None:
			# 只读检查先于队列的 executing 写入；底层派发检查不变（Issue #2577）。
			self._check_local_risk()

	def scan_conversations(
		self,
		tabs: list[str],
		max_per_tab: int,
	) -> list[ConversationRef]:
		recruiter = self._recruiter
		if recruiter is None:
			return [_diagnostic_ref("zhipin", self._warning)]
		self._ensure_not_risk_stopped()
		# tabs 是操作者勾选的扫描范围：空配置或不匹配的 tab 不能回退到未读列表。
		# 每个 tab 单独截断，避免「新招呼」占满上限后「未读」完全扫不到（Issue #1581）。
		refs: list[ConversationRef] = []
		seen_ids: set[str] = set()
		if "新招呼" in tabs:
			items = _items_from_response(self._checked_response(lambda: recruiter.greet_list(page=1)))
			refs.extend(_ref_from_item(item) for item in items[:max_per_tab])
		if "未读" in tabs:
			items = _items_from_response(self._checked_response(lambda: recruiter.friend_list(page=1)))
			refs.extend(_ref_from_item(item) for item in items[:max_per_tab])
		# 同一候选可能同时出现在两个 tab：只按最新勾选的 tab 处理一次，避免一轮内
		# 对同一会话读取两次历史、甚至重复派发写动作。
		unique: list[ConversationRef] = []
		for ref in refs:
			if ref.id in seen_ids:
				continue
			seen_ids.add(ref.id)
			unique.append(ref)
		return unique

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		self._ensure_not_risk_stopped()
		if ref.conversation is not None:
			return ref.conversation
		recruiter = self._recruiter
		if recruiter is None:
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		gid = _friend_id(ref.id)
		if gid is None:
			return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))
		return _conversation_from_history(ref.id, gid, self._checked_response(lambda: recruiter.chat_history(gid)))

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
		if action is PlatformAction.CREATE_INTERVIEW_LEAD:
			return ActionResult("executed", {"local_only": True})
		friend_id = _friend_id(ref.id)
		if friend_id is None:
			return ActionResult("blocked", {"reason": _INVALID_FRIEND_ID})
		match action:
			case PlatformAction.SEND_QUESTIONNAIRE | PlatformAction.SEND_FOLLOW_UP:
				result = self._checked_response(
					lambda: recruiter.send_message_by_friend(friend_id, message), require_success=False
				)
			case PlatformAction.EXCHANGE_CONTACT:
				result = self._checked_response(
					lambda: recruiter.exchange_request_by_friend(friend_id, 2), require_success=False
				)
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
		if require_success and not (is_platform_success_code(response.get("code")) or response.get("ok") is True):
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
		# chat_history 的真实结构是 zpData.messages；列表类接口仍走前面的键。
		for key in ("list", "result", "items", "geekList", "friendList", "messages"):
			value = data.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]
	return []


def _ref_from_item(item: dict[str, Any]) -> ConversationRef:
	for key in ("friendId", "gid", "id", "uid"):
		raw_id = item.get(key)
		# null / 空串 / 0 是「这个字段没填」，沿用原先按顺序回退的口径；
		# 非空但不合法的标识会直接停下，见下方 break。
		if raw_id is None or raw_id == "" or raw_id == 0:
			continue
		friend_id = _friend_id(raw_id)
		if friend_id is not None:
			# 保留已有字符串身份，避免改变历史队列与会话状态的匹配键。
			ref_id = str(raw_id)
			name = item.get("name") or item.get("geekName")
			title = name if isinstance(name, str) else ref_id
			return ConversationRef(id=ref_id, tab="scan", reason=title)
		# 主要标识存在但不可信：不能改用可能属于别的空间的低优先级标识。
		break
	# 展示名不是会话地址；无效条目只出诊断，不进入读取或写入路径（Issue #2580）。
	return _diagnostic_ref("zhipin-invalid-friend-id", _INVALID_FRIEND_ID)


def _conversation_from_history(ref_id: str, friend_id: int, response: dict[str, Any]) -> Conversation:
	items = _items_from_response(response)
	messages: list[tuple[str, str]] = []
	for item in items:
		direction = _message_direction(item, friend_id)
		if direction is None:
			# 系统消息（from.uid=0）不是候选人发言，不能触发回复决策。
			continue
		messages.append((direction, _message_text(item)))
	ordered = tuple(messages)
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


def _message_direction(item: dict[str, Any], friend_id: int) -> str | None:
	"""判断一条聊天记录是候选人 / 招聘者 / 系统消息。

	真实结构用字典发送者 ``from: {uid, source}``：uid 为会话好友的是候选人，
	uid=0 是系统消息，其余是招聘者本人（与 ``recruiter_resume.sent_by_self``
	同口径）。旧的合成结构用 ``"boss"`` / ``"self"`` / ``1`` 表示招聘者，
	没有 ``from`` 的条目沿用「按候选人消息处理」（Issue #2582）。
	"""
	sender = item.get("from")
	if isinstance(sender, dict):
		uid = _sender_uid(sender.get("uid"))
		if uid is None or uid == 0:
			return None
		return "incoming" if uid == friend_id else "outgoing"
	if isinstance(sender, str):
		return "outgoing" if sender in {"boss", "self"} else "incoming"
	if isinstance(sender, (int, bool)):
		return "outgoing" if sender == 1 else "incoming"
	return "incoming"


def _sender_uid(value: Any) -> int | None:
	"""发送者 uid：只接受非负整数或 ASCII 十进制字符串，形状漂移按无法判断处理。"""
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value if value >= 0 else None
	if isinstance(value, str) and value.isascii() and value.isdecimal():
		try:
			return int(value)
		except ValueError:
			# 超过 int 转换上限的数字串不是可用 uid。
			return None
	return None


def _message_text(item: dict[str, Any]) -> str:
	"""取消息正文：兼容旧结构的 content / text 与真实的 body / pushText。"""
	for key in ("content", "text"):
		value = item.get(key)
		if isinstance(value, str) and value:
			return value
	return "\n".join(text for text in message_texts(item) if text)


def _friend_id(value: Any) -> int | None:
	if type(value) is int:
		return value if value > 0 and safe_text(value) else None
	if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
		return None
	try:
		parsed = int(value)
	except ValueError:
		return None
	return parsed if parsed > 0 and safe_text(parsed) else None


def _action_result(response: dict[str, Any]) -> ActionResult:
	code = response.get("code")
	if is_platform_success_code(code) or response.get("ok") is True:
		return ActionResult("executed", {"response": response})
	if response.get("__cli_error_code__") == "ACTION_UNCONFIRMED":
		# 页面动作可能已经生效：这不是「确定没发出」的普通失败，
		# 标成 blocked 会让待执行队列在下一轮自动重发。
		return ActionResult("unconfirmed", {"reason": "ACTION_UNCONFIRMED"})
	return ActionResult("blocked", {"reason": str(response.get("message", code))})
