"""Automation configuration parsing with conservative defaults."""

from __future__ import annotations

import math
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


_DEFAULT = AutomationConfig()


def _config_int(data: dict[str, Any], key: str, default: int, *, minimum: int) -> int:
	"""读取整数字段并校验下界。

	这些字段直接决定「处理多少」，负数会让下游的比较与切片反转语义：
	`max_per_tab=-1` 传给 `items[:-1]` 等于「去掉最后 1 个」。
	"""
	value = data.get(key, default)
	if isinstance(value, bool):
		raise ValueError(f"{key} 必须是整数，收到 {value!r}")
	try:
		parsed = int(value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{key} 必须是整数，收到 {value!r}") from exc
	if parsed < minimum:
		raise ValueError(f"{key} 必须是不小于 {minimum} 的整数，收到 {value!r}")
	return parsed


def _config_confidence(data: dict[str, Any], key: str, default: float) -> float:
	"""读取置信度阈值并限制在 [0, 1]。

	阈值写坏会直接改变执行判定：`auto_execute_threshold=-1` 让所有通过初筛的
	动作都自动执行（真实的写操作），`human_review_threshold=-1` 则连低置信度
	决策也放行。
	"""
	value = data.get(key, default)
	if isinstance(value, bool):
		raise ValueError(f"{key} 必须是 0 到 1 之间的数值，收到 {value!r}")
	try:
		parsed = float(value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{key} 必须是 0 到 1 之间的数值，收到 {value!r}") from exc
	if not math.isfinite(parsed) or not 0 <= parsed <= 1:
		raise ValueError(f"{key} 必须是 0 到 1 之间的数值，收到 {value!r}")
	return parsed


def _config_tabs(data: dict[str, Any]) -> tuple[str, ...]:
	"""读取要扫描的 tab 列表，必须是字符串数组。

	直接 `tuple(str(item) for item in value)` 会在用户把 ``tabs`` 写成字符串时
	按字符拆开：``"新招呼"`` 变成 ``("新", "招", "呼")``——执行层匹配不到任何
	tab，自动化空转却报成功；写成 ``null`` 或数字则抛未包装的 TypeError。
	"""
	value = data.get("tabs", _DEFAULT.tabs)
	if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
		raise ValueError(f"tabs 必须是字符串数组，收到 {value!r}")
	return tuple(value)


def _config_text(data: dict[str, Any], key: str, default: str) -> str:
	"""读取发给候选人的话术，必须是非空字符串。

	`str(value)` 会把 ``null`` / 数字静默变成 ``"None"`` / ``"123"``，自动化随后
	把它们原样发给候选人；话术还用于匹配历史消息推断阶段，空值会破坏判定。
	"""
	value = data.get(key, default)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{key} 必须是非空字符串，收到 {value!r}")
	return value


def automation_config_from_dict(raw: dict[str, Any] | None) -> AutomationConfig:
	"""Parse automation config from config.json-compatible data."""
	data = raw or {}
	if not isinstance(data, dict):
		# 手工编辑过的 config.json 可能把 automation 写成字符串；这里给出可读错误，
		# 而不是在 .get() 上抛 AttributeError。
		raise ValueError("automation 配置必须是 JSON object")
	default_actions = [action.value for action in DEFAULT_ALLOWED_ACTIONS]
	allowed_action_values = {action.value for action in PlatformAction}
	# 安全相关配置必须 fail-closed：旧实现把未知值静默过滤后 `actions or DEFAULT_ALLOWED_ACTIONS`
	# 回退到默认全集，用户写错一个枚举值（或写空列表想禁用自动动作）反而放开全部写动作。
	# 未知值一律报错；空列表保留为空集合——执行层会把每个动作判为 SKIPPED。
	raw_actions = data.get("allowed_actions", default_actions)
	if not isinstance(raw_actions, (list, tuple)):
		raise ValueError(f"allowed_actions 必须是字符串数组，收到 {raw_actions!r}")
	unknown_actions = [item for item in raw_actions if item not in allowed_action_values]
	if unknown_actions:
		raise ValueError(
			f"allowed_actions 包含未知动作：{unknown_actions}；"
			f"有效值：{sorted(allowed_action_values)}"
		)
	actions = tuple(PlatformAction(item) for item in raw_actions)
	human_review_threshold = _config_confidence(
		data, "human_review_threshold", _DEFAULT.human_review_threshold
	)
	auto_execute_threshold = _config_confidence(
		data, "auto_execute_threshold", _DEFAULT.auto_execute_threshold
	)
	if human_review_threshold > auto_execute_threshold:
		raise ValueError(
			f"human_review_threshold（{human_review_threshold}）不能大于 "
			f"auto_execute_threshold（{auto_execute_threshold}）"
		)
	return AutomationConfig(
		mode=AutomationMode(data.get("mode", _DEFAULT.mode.value)),
		allowed_actions=actions,
		human_review_threshold=human_review_threshold,
		auto_execute_threshold=auto_execute_threshold,
		max_actions_per_run=_config_int(data, "max_actions_per_run", _DEFAULT.max_actions_per_run, minimum=0),
		max_consecutive_errors=_config_int(
			data, "max_consecutive_errors", _DEFAULT.max_consecutive_errors, minimum=0
		),
		tabs=_config_tabs(data),
		max_per_tab=_config_int(data, "max_per_tab", _DEFAULT.max_per_tab, minimum=0),
		questionnaire_message=_config_text(
			data, "questionnaire_message", _DEFAULT.questionnaire_message
		),
		follow_up_message=_config_text(
			data, "follow_up_message", _DEFAULT.follow_up_message
		),
		reply_strategy=ReplyStrategy(data.get("reply_strategy", _DEFAULT.reply_strategy.value)),
	)
