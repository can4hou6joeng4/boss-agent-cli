"""向导恢复指引必须可直接重放（Issue #3155）。"""

import pytest

from boss_agent_cli.display import boss_command
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(data_dir):
	return ActionContext(
		data_dir=data_dir,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
	)


def test_corrupt_resume_hint_protects_leading_dash(tmp_path):
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "-x.json").write_text("{", encoding="utf-8")

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "-x", "prompt": "优化"}, {})

	expected = boss_command("resume delete -- -x", platform_name="zhipin", data_dir=tmp_path.resolve())
	assert expected in caught.value.recovery_action


def test_missing_crawl_run_hint_keeps_data_dir_and_protects_run_id(tmp_path):
	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["crawl_status"](_context(tmp_path), {"run_id": "-ghost"}, {})

	expected_status = boss_command("crawl status -- -ghost", platform_name="zhipin", data_dir=tmp_path.resolve())
	expected_start = boss_command("crawl start <query>", platform_name="zhipin", data_dir=tmp_path.resolve())
	assert caught.value.code == "CRAWL_RUN_NOT_FOUND"
	assert expected_status in caught.value.recovery_action
	assert expected_start in caught.value.recovery_action
