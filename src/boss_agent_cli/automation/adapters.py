"""Automation platform adapter factory and protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationRef,
	PlatformAction,
	PlatformHealth,
)


class RecruiterAutomationPlatform(Protocol):
	"""Unified recruiter automation adapter surface."""

	name: str

	def ensure_session(self) -> None: ...
	def scan_conversations(
		self,
		tabs: list[str],
		max_per_tab: int,
	) -> list[ConversationRef]: ...
	def read_conversation(self, ref: ConversationRef) -> Conversation: ...
	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult: ...
	def health_check(self) -> PlatformHealth: ...
	def detect_safety_warning(self) -> str | None: ...


def build_automation_adapter(
	platform_name: str,
	*,
	data_dir: Path | None = None,
	delay: tuple[float, float] = (1.5, 3.0),
	cdp_url: str | None = None,
	live: bool = False,
	sample_conversations: list[Conversation] | None = None,
) -> RecruiterAutomationPlatform:
	"""Return the recruiter automation adapter for a platform."""
	if sample_conversations is not None:
		from boss_agent_cli.automation.mock_adapter import (
			MockRecruiterAutomationPlatform,
		)

		return MockRecruiterAutomationPlatform(platform_name, sample_conversations)
	match platform_name:
		case "zhilian":
			from boss_agent_cli.automation.zhilian_adapter import (
				ZhilianRecruiterAutomationPlatform,
			)

			return ZhilianRecruiterAutomationPlatform.from_context(
				data_dir=data_dir,
				delay=delay,
				cdp_url=cdp_url,
				live=live,
			)
		case "zhipin":
			from boss_agent_cli.automation.boss_adapter import (
				BossRecruiterAutomationPlatform,
			)

			return BossRecruiterAutomationPlatform.from_context(
				data_dir=data_dir,
				delay=delay,
				cdp_url=cdp_url,
				live=live,
			)
		case _:
			from boss_agent_cli.automation.mock_adapter import (
				MockRecruiterAutomationPlatform,
			)

			return MockRecruiterAutomationPlatform(
				platform_name,
				warning=f"unsupported automation platform: {platform_name}",
			)
