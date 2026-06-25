"""Local-AI reply drafting for recruiter automation."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.models import Conversation, Decision, PlatformAction

_LOCAL_AI_PROVIDERS = {"ollama", "vllm"}


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
		max_tokens=512,
	)
	try:
		raw = service.chat(_reply_messages(decision, conversation))
	except AIServiceError:
		return replace(decision, reason=f"{decision.reason}; local ai unavailable, template fallback")
	try:
		draft = parse_reply_draft(raw)
	except (JSONDecodeError, KeyError, TypeError):
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
	"""Parse the strict JSON object produced by a local model."""
	text = raw.strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
	data = json.loads(text)
	if not isinstance(data, dict):
		raise TypeError("reply draft must be a JSON object")
	risk_flags = data.get("risk_flags", [])
	if not isinstance(risk_flags, list):
		raise TypeError("risk_flags must be a list")
	return ReplyDraft(
		action=str(data["action"]),
		confidence=float(data["confidence"]),
		reply=str(data["reply"]),
		reason=str(data["reason"]),
		risk_flags=tuple(str(item) for item in risk_flags),
	)


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
