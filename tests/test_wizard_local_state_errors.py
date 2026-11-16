"""wizard 本地简历损坏与联系人查找上限的错误语义（Issue #1331）。"""

import pytest

import boss_agent_cli.wizard.actions as actions
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path):
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
	)


def test_ai_assist_reports_corrupt_resume_as_file_invalid(tmp_path):
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "broken.json").write_text("{not json", encoding="utf-8")

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "broken", "prompt": "优化"}, {})

	assert exc_info.value.code == "RESUME_FILE_INVALID"
	assert "损坏" in str(exc_info.value)
	assert "resume delete" in exc_info.value.recovery_action
	assert str(tmp_path.resolve()) in exc_info.value.recovery_action


def test_ai_assist_keeps_resume_not_found_for_missing_file(tmp_path):
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "nope", "prompt": "优化"}, {})
	assert exc_info.value.code == "RESUME_NOT_FOUND"


def test_local_resumes_exposes_corrupt_files(tmp_path):
	resumes = tmp_path / "resumes"
	ResumeStore(resumes).save(ResumeData(name="default", title="后端工程师"))
	(resumes / "broken.json").write_text("{not json", encoding="utf-8")

	result = DEFAULT_ACTIONS["local_resumes"](_context(tmp_path), {}, {})

	assert [item["name"] for item in result.data["items"]] == ["default"]
	assert result.data["corrupt"] == ["broken"]


def test_local_resumes_omits_corrupt_key_when_clean(tmp_path):
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))

	result = DEFAULT_ACTIONS["local_resumes"](_context(tmp_path), {}, {})

	assert "corrupt" not in result.data


def test_friend_lookup_limit_points_to_chat_uid_retry(tmp_path, monkeypatch):
	def _exceeded(*args, **kwargs):
		raise FriendLookupLimitExceeded("沟通列表分页遍历超过上限，未能确认联系人是否存在，请重试")

	monkeypatch.setattr(actions, "find_friend", _exceeded)

	with pytest.raises(WorkflowActionError) as exc_info:
		actions._candidate_friend(object(), "rotating-security-id", context=_context(tmp_path))

	error = exc_info.value
	assert error.code == "NETWORK_ERROR"
	assert error.recoverable is True
	assert "chat" in error.recovery_action
	assert "uid" in error.recovery_action
	assert "缩小沟通列表范围" not in error.recovery_action
	assert str(tmp_path.resolve()) in error.recovery_action
