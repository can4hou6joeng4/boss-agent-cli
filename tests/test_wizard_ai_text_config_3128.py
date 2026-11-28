"""向导 ai_assist 的字符串配置项类型漂移必须确定性报错（Issue #3128）。"""

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


@pytest.mark.parametrize(
	("key", "bad_value", "fix_option"),
	[
		("ai_model", ["gpt-4o"], "--model"),
		("ai_model", 1, "--model"),
		("ai_provider", ["openai"], "--provider"),
	],
)
def test_text_config_type_drift_is_config_invalid(tmp_path, monkeypatch, key, bad_value, fix_option):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o", ai_base_url="http://127.0.0.1:1/v1")
	store.save_config(**{key: bad_value})
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
	assert key in str(error)
	assert fix_option in error.recovery_action
