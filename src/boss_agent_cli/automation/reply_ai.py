"""Local-AI reply drafting for recruiter automation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.local_models import RUNTIME_BASE_URLS
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.models import Conversation, Decision, PlatformAction

# 本地 reply provider 集合 == 本地模型运行时集合（单一真源在 ai.local_models）
_LOCAL_AI_PROVIDERS = frozenset(RUNTIME_BASE_URLS)


@dataclass(frozen=True, slots=True)
class ReplyDraft:
	action: str
	confidence: float
	reply: str
	reason: str
	risk_flags: tuple[str, ...]


def apply_reply_strategy(
	decision: Decision,
	conversation: Conversation,
	config: AutomationConfig,
	data_dir: Path,
) -> Decision:
	"""Apply local-AI reply drafting while preserving the rule-chosen action."""
	if not _can_draft(decision):
		return decision
	match config.reply_strategy:
		case ReplyStrategy.TEMPLATE:
			return decision
		case ReplyStrategy.HYBRID | ReplyStrategy.LOCAL_AI:
			return _draft_with_local_ai(decision, conversation, config, data_dir)
		case unreachable:
			return unreachable


def _can_draft(decision: Decision) -> bool:
	return bool(
		decision.message
		and decision.action in {PlatformAction.SEND_QUESTIONNAIRE, PlatformAction.SEND_FOLLOW_UP}
	)


def _draft_with_local_ai(
	decision: Decision,
	conversation: Conversation,
	config: AutomationConfig,
	data_dir: Path,
) -> Decision:
	store = AIConfigStore(data_dir)
	ai_config = store.load_config()
	provider = str(ai_config.get("ai_provider", ""))
	if provider not in _LOCAL_AI_PROVIDERS:
		return decision
	api_key = store.get_api_key()
	base_url = store.get_base_url()
	model = ai_config.get("ai_model")
	if not api_key or not base_url or not model:
		return decision
	service = AIService(
		base_url=base_url,
		api_key=api_key,
		model=str(model),
		temperature=0.3,
		# 上限跟随配置（ai local configure 写入 512）：思考模型写不完时可用 boss ai config --max-tokens 调大
		max_tokens=int(ai_config.get("ai_max_tokens") or 512),
	)
	try:
		raw = service.chat(_reply_messages(decision, conversation))
	except AIServiceError as exc:
		return replace(decision, reason=f"{decision.reason}; local ai unavailable ({exc}), template fallback")
	try:
		draft = parse_reply_draft(raw)
	except (JSONDecodeError, KeyError, TypeError, ValueError):
		# 本地模型输出是不可信外部输入：`confidence: "high"` 的 float() 会抛
		# ValueError，旧实现没接住，一个确定性的格式问题会终止整轮自动化并被
		# CLI 报成可重试的 NETWORK_ERROR（Issue #893）。所有结构错误统一降级到
		# 模板话术并要求人工复核，不发送任何消息。
		return replace(
			decision,
			confidence=min(decision.confidence, 0.5),
			requires_human=True,
			reason=f"{decision.reason}; local ai parse failed",
			risk_flags=(*decision.risk_flags, "local-ai-parse-error"),
		)
	if draft.action != decision.action.value:
		return replace(
			decision,
			requires_human=True,
			reason=f"{decision.reason}; local ai action mismatch: {draft.action}",
			risk_flags=(*decision.risk_flags, "local-ai-action-mismatch"),
		)
	if draft.confidence < config.human_review_threshold or draft.risk_flags:
		return replace(
			decision,
			message=draft.reply or decision.message,
			confidence=min(decision.confidence, draft.confidence),
			requires_human=True,
			reason=f"{decision.reason}; local ai review: {draft.reason}",
			risk_flags=(*decision.risk_flags, *draft.risk_flags),
		)
	if not draft.reply:
		return decision
	return replace(
		decision,
		message=draft.reply,
		confidence=min(decision.confidence, draft.confidence),
		reason=f"{decision.reason}; local ai reply: {draft.reason}",
	)


def parse_reply_draft(raw: str) -> ReplyDraft:
	"""Parse the strict JSON object produced by a local model.

	模型输出是外部输入，字段类型必须显式校验：``reply: null`` 经 ``str()`` 会变成
	``"None"`` 并作为话术原样发给候选人（``123`` 同理）；``confidence: "high"``
	会让 ``float()`` 抛出调用方没接住的 ValueError；``2.0`` / ``NaN`` 则绕开草稿
	自身的复核分支。这里把结构问题抛给调用方，由调用方统一降级到模板话术
	（Issue #893）。
	"""
	text = raw.strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
	data = json.loads(text)
	if not isinstance(data, dict):
		raise TypeError("reply draft must be a JSON object")
	action = data["action"]
	reply = data["reply"]
	if not isinstance(action, str):
		raise TypeError("reply draft action must be a string")
	if not isinstance(reply, str):
		raise TypeError("reply draft reply must be a string")
	confidence = _draft_confidence(data["confidence"])
	risk_flags = data.get("risk_flags", [])
	if not isinstance(risk_flags, list):
		raise TypeError("risk_flags must be a list")
	return ReplyDraft(
		action=action,
		confidence=confidence,
		# 首尾空白对聊天话术没有意义；全空白回复按空串处理，调用方退回模板。
		reply=reply.strip(),
		reason=str(data["reason"]),
		risk_flags=tuple(str(item) for item in risk_flags),
	)


def _draft_confidence(value: Any) -> float:
	"""Validate model-reported confidence as a finite number in [0, 1].

	接受可无损转成数值的字符串（部分本地模型会把 ``0.9`` 写成 ``"0.9"``），
	但拒绝布尔值、非数值文本与越界值——这些都不是可解释的置信度。
	"""
	if isinstance(value, bool):
		raise TypeError("reply draft confidence must be a number")
	try:
		confidence = float(value)
	except (TypeError, ValueError) as exc:
		raise ValueError("reply draft confidence must be a finite number between 0 and 1") from exc
	if not math.isfinite(confidence) or not 0 <= confidence <= 1:
		raise ValueError("reply draft confidence must be between 0 and 1")
	return confidence


def _reply_messages(decision: Decision, conversation: Conversation) -> list[dict[str, Any]]:
	transcript = "\n".join(conversation.all_messages or conversation.incoming_messages or conversation.outgoing_messages)
	payload = {
		"chosen_action": decision.action.value,
		"template_reply": decision.message,
		"conversation_title": conversation.title,
		"item_title": conversation.item_title,
		"transcript": transcript[-3000:],
		"output_schema": {
			"action": decision.action.value,
			"confidence": 0.0,
			"reply": "string",
			"reason": "string",
			"risk_flags": ["string"],
		},
	}
	return [
		{
			"role": "system",
			"content": (
				"你是招聘者自动回复文案助手。规则系统已经决定动作，"
				"你只能润色 reply，不能改变 action。只返回 JSON。"
			),
		},
		{"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
	]
