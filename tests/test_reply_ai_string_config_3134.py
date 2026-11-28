"""自动回复的字符串配置漂移必须模板降级而非把坏值发给服务（Issue #3134）。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.automation.config import AutomationConfig, ReplyStrategy
from boss_agent_cli.automation.models import CandidateKey, Conversation, Decision, PlatformAction
from boss_agent_cli.automation import reply_ai


def _decision() -> Decision:
	return Decision(
		action=PlatformAction.SEND_FOLLOW_UP,
		confidence=0.9,
		reason="规则命中",
		candidate_key=CandidateKey("synthetic"),
		message="模板话术",
	)


@pytest.mark.parametrize(
	("key", "bad_value"),
	[("ai_model", ["qwen3:14b"]), ("ai_model", 1), ("ai_provider", ["ollama"])],
)
def test_string_drift_falls_back_without_request(tmp_path: Path, monkeypatch, key, bad_value):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_api_key("synthetic-key")
	store.save_config(ai_provider="ollama", ai_model="qwen3:14b", ai_base_url="http://127.0.0.1:1/v1")
	store.save_config(**{key: bad_value})
	chat = Mock(return_value=json.dumps({
		"action": "send_follow_up", "confidence": 0.9,
		"reply": "不应使用", "reason": "x", "risk_flags": [],
	}))
	monkeypatch.setattr(reply_ai.AIService, "chat", chat)

	result = reply_ai._draft_with_local_ai(
		_decision(), Conversation(title="synthetic"),
		AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI), tmp_path,
	)

	assert result.message == "模板话术"
	assert "local ai config invalid" in result.reason
	assert "template fallback" in result.reason
	chat.assert_not_called()


@pytest.mark.parametrize("bad_key", ["   ", "\t\n"])
def test_blank_key_falls_back_without_request(tmp_path: Path, monkeypatch, bad_key):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_api_key(bad_key)
	store.save_config(ai_provider="ollama", ai_model="qwen3:14b", ai_base_url="http://127.0.0.1:1/v1")
	chat = Mock()
	monkeypatch.setattr(reply_ai.AIService, "chat", chat)

	result = reply_ai._draft_with_local_ai(
		_decision(), Conversation(title="synthetic"),
		AutomationConfig(reply_strategy=ReplyStrategy.LOCAL_AI), tmp_path,
	)

	assert result.message == "模板话术"
	chat.assert_not_called()
