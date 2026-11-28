"""向导 ai_assist 的纯空白 API key 必须按未配置处理（Issue #3130）。"""

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.wizard import actions
from boss_agent_cli.wizard.runner import WorkflowActionError


@pytest.mark.parametrize("bad_key", ["", "   ", "\t\n"])
def test_blank_api_key_is_not_configured(tmp_path, monkeypatch, bad_key):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o", ai_base_url="http://127.0.0.1:1/v1")
	store.save_api_key(bad_key)
	ctx = actions.ActionContext(
		data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger(),
		candidate_factory=lambda *a, **k: None,
	)

	class _ForbiddenService:
		def __init__(self, *args, **kwargs):
			pytest.fail("空白 API key 不能在发请求前构造 AIService")

	monkeypatch.setattr(actions, "AIService", _ForbiddenService)
	with pytest.raises(WorkflowActionError) as exc_info:
		actions.DEFAULT_ACTIONS["ai_assist"](ctx, {"resume": "default", "prompt": "优化"}, {})

	error = exc_info.value
	assert error.code == "AI_NOT_CONFIGURED"
	assert error.recoverable is True
	assert "ai config" in error.recovery_action
