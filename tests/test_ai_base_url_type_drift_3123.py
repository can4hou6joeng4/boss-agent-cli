"""AI base_url 的伪值类型必须报本地配置错误，不能回落到 provider 默认端点（Issue #3123）。"""

import json

import pytest

from boss_agent_cli.ai.config import AIConfigStore, AIConfigValueError


def _store(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine-id")
	return AIConfigStore(tmp_path)


@pytest.mark.parametrize("bad", [0, False, [], {}, 1])
def test_falsy_base_url_type_drift_must_not_fall_back_to_provider(tmp_path, monkeypatch, bad):
	store = _store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	(tmp_path / "ai" / "config.json").write_text(
		json.dumps({"ai_provider": "openai", "ai_model": "gpt-4o", "ai_base_url": bad}),
		encoding="utf-8",
	)
	with pytest.raises(AIConfigValueError, match="ai_base_url"):
		store.get_base_url()


def test_explicit_empty_base_url_still_uses_provider(tmp_path, monkeypatch):
	"""显式空串仍是「未设置」，与写入侧口径一致，不要误报配置损坏。"""
	store = _store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	(tmp_path / "ai" / "config.json").write_text(
		json.dumps({"ai_provider": "openai", "ai_model": "gpt-4o", "ai_base_url": ""}),
		encoding="utf-8",
	)
	assert store.get_base_url() == "https://api.openai.com/v1"
