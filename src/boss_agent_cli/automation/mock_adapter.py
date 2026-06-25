"""Mock recruiter automation adapter for tests and dry-run fixtures."""

from __future__ import annotations

from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	PlatformAction,
	PlatformHealth,
)


class MockRecruiterAutomationPlatform:
	"""Deterministic adapter for tests and explicit fixture runs."""

	def __init__(
		self,
		name: str,
		conversations: list[Conversation] | None = None,
		warning: str = "",
	) -> None:
		self.name = name
		self._conversations = conversations or _default_conversations(name)
		self._warning = warning
		self.executed: list[tuple[PlatformAction, str]] = []

	def ensure_session(self) -> None:
		return None

	def scan_conversations(
		self,
		tabs: list[str],
		max_per_tab: int,
	) -> list[ConversationRef]:
		refs: list[ConversationRef] = []
		for index, conversation in enumerate(self._conversations[:max_per_tab]):
			tab = tabs[index % len(tabs)] if tabs else "default"
			refs.append(
				ConversationRef(
					id=conversation.title or f"{self.name}-{index}",
					tab=tab,
					conversation=conversation,
				)
			)
		return refs

	def read_conversation(self, ref: ConversationRef) -> Conversation:
		if ref.conversation is not None:
			return ref.conversation
		return Conversation(title=ref.id, fingerprint=ConversationFingerprint(ref.id))

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.executed.append((action, ref.id))
		return ActionResult(
			status="executed",
			details={
				"action": action.value,
				"message": message,
				"conversation": ref.id,
			},
		)

	def health_check(self) -> PlatformHealth:
		if self._warning:
			return PlatformHealth(status="needs-human-verification", warning=self._warning)
		return PlatformHealth(status="healthy", checks=({"name": "mock", "ok": True},))

	def detect_safety_warning(self) -> str | None:
		return self._warning or None


def _default_conversations(platform: str) -> list[Conversation]:
	return [
		Conversation(
			title=f"{platform}-高分候选人",
			incoming_messages=(
				"你好，我在上海做过3年销售，擅长客户沟通，"
				"大专，期望10-12k，想看机会",
			),
			ordered_messages=(
				(
					"incoming",
					"你好，我在上海做过3年销售，擅长客户沟通，"
					"大专，期望10-12k，想看机会",
				),
			),
			all_messages=("上海 3年销售 客户沟通 大专 想看机会",),
			fingerprint=ConversationFingerprint(f"{platform}-candidate-1"),
		),
		Conversation(
			title=f"{platform}-风险候选人",
			incoming_messages=("不要再联系",),
			ordered_messages=(("incoming", "不要再联系"),),
			all_messages=("不要再联系",),
			fingerprint=ConversationFingerprint(f"{platform}-candidate-2"),
		),
	]
