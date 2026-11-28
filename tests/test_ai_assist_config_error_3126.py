"""向导 ai_assist 的 base_url 本地配置错误必须收口为 CONFIG_INVALID（Issue #3126）。"""

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


@pytest.mark.parametrize("bad_base_url", [0, "localhost:11434"])
def test_ai_assist_base_url_error_is_config_invalid(tmp_path, monkeypatch, bad_base_url):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o", ai_base_url=bad_base_url)
	store.save_api_key("sk-test")
	ctx = ActionContext(
		data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger(),
		candidate_factory=lambda *a, **k: None,
	)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](ctx, {"resume": "default", "prompt": "优化"}, {})

	error = exc_info.value
	assert error.code == "CONFIG_INVALID"
	assert error.recoverable is True
	assert error.auto_retry is False
	assert "ai config" in error.recovery_action
	assert "ai_base_url" in str(error)
