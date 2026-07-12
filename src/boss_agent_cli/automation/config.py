"""Automation configuration parsing with conservative defaults."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Final

from boss_agent_cli.automation.models import AutomationMode, PlatformAction

DEFAULT_ALLOWED_ACTIONS: Final = (
	PlatformAction.SCAN_CONVERSATIONS,
	PlatformAction.READ_CANDIDATE_PROFILE,
	PlatformAction.SEND_QUESTIONNAIRE,
	PlatformAction.SEND_FOLLOW_UP,
	PlatformAction.EXCHANGE_CONTACT,
	PlatformAction.CREATE_INTERVIEW_LEAD,
)


@unique
class ReplyStrategy(str, Enum):
	TEMPLATE = "template"
	LOCAL_AI = "local_ai"
	HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class AutomationConfig:
	mode: AutomationMode = AutomationMode.AUTONOMOUS
	platforms: tuple[str, ...] = ("zhilian", "zhipin")
	allowed_actions: tuple[PlatformAction, ...] = DEFAULT_ALLOWED_ACTIONS
	human_review_threshold: float = 0.65
	auto_execute_threshold: float = 0.82
	max_actions_per_run: int = 50
	max_consecutive_errors: int = 3
	tabs: tuple[str, ...] = ("新招呼", "未读")
	max_per_tab: int = 20
	questionnaire_message: str = "您好，想确认下近期是否看机会？"
	follow_up_message: str = (
		"谢谢回复，我这边同步岗位信息，方便的话可以继续沟通面试时间。"
	)
	reply_strategy: ReplyStrategy = ReplyStrategy.HYBRID
	stop_on_page_text: tuple[str, ...] = (
		"验证码",
		"安全验证",
		"操作频繁",
		"账号异常",
		"访问受限",
	)


_DEFAULT = AutomationConfig()


def automation_config_from_dict(raw: dict[str, Any] | None) -> AutomationConfig:
	"""Parse automation config from config.json-compatible data."""
	data = raw or {}
	default_actions = [action.value for action in DEFAULT_ALLOWED_ACTIONS]
	allowed_action_values = {action.value for action in PlatformAction}
	actions = tuple(
		PlatformAction(item)
		for item in data.get("allowed_actions", default_actions)
		if item in allowed_action_values
	)
	return AutomationConfig(
		mode=AutomationMode(data.get("mode", _DEFAULT.mode.value)),
		platforms=tuple(str(item) for item in data.get("platforms", _DEFAULT.platforms)),
		allowed_actions=actions or DEFAULT_ALLOWED_ACTIONS,
		human_review_threshold=float(data.get("human_review_threshold", _DEFAULT.human_review_threshold)),
		auto_execute_threshold=float(data.get("auto_execute_threshold", _DEFAULT.auto_execute_threshold)),
		max_actions_per_run=int(data.get("max_actions_per_run", _DEFAULT.max_actions_per_run)),
		max_consecutive_errors=int(data.get("max_consecutive_errors", _DEFAULT.max_consecutive_errors)),
		tabs=tuple(str(item) for item in data.get("tabs", _DEFAULT.tabs)),
		max_per_tab=int(data.get("max_per_tab", _DEFAULT.max_per_tab)),
		questionnaire_message=str(data.get("questionnaire_message", _DEFAULT.questionnaire_message)),
		follow_up_message=str(data.get("follow_up_message", _DEFAULT.follow_up_message)),
		reply_strategy=ReplyStrategy(data.get("reply_strategy", _DEFAULT.reply_strategy.value)),
		stop_on_page_text=tuple(str(item) for item in data.get("stop_on_page_text", _DEFAULT.stop_on_page_text)),
	)
