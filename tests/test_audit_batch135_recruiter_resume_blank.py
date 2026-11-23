"""审计批次 135：向导 recruiter_resume 拒绝空白 geek_id / job_id（Issue #2525）。

CLI `hr resume` 在入口要求 geek_id / job_id / --security-id 去空白后非空
（`commands/recruiter/resume.py`，Issue #2069 口径）。向导的 `_recruiter_resume`
仍只查类型：空白标识会照常发起一次 `effect: unknown` 的 view_geek 请求。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


class _FakeRecruiterPlatform:
	def __init__(self) -> None:
		self.calls: list[tuple[object, ...]] = []

	def __enter__(self) -> "_FakeRecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, object]:
		self.calls.append(("view_geek", geek_id, job_id, security_id))
		return {"code": 0, "data": {"resume": True}}

	def is_success(self, response: dict[str, object]) -> bool:
		return response["code"] == 0

	def unwrap_data(self, response: dict[str, object]) -> object:
		return response["data"]


def _forbidden_factory(*args: object, **kwargs: object) -> object:
	raise AssertionError("非法输入不应构造平台实例")


def _context(tmp_path: Path, *, recruiter: object | None = None) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger(),
		candidate_factory=_forbidden_factory,
		recruiter_factory=(lambda *args, **kwargs: recruiter) if recruiter is not None else _forbidden_factory,
	)


@pytest.mark.parametrize("key", ["geek_id", "job_id"])
def test_recruiter_resume_rejects_blank_identifier(tmp_path: Path, key: str) -> None:
	inputs = {"geek_id": "synthetic-geek", "job_id": "synthetic-job"}
	inputs[key] = "   "

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["recruiter_resume"](_context(tmp_path), inputs, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert key in str(exc_info.value)


def test_recruiter_resume_preserves_padded_identifiers(tmp_path: Path) -> None:
	"""CLI 只判 ``.strip()``、不改写参数；向导必须原样传给平台。"""
	platform = _FakeRecruiterPlatform()

	DEFAULT_ACTIONS["recruiter_resume"](
		_context(tmp_path, recruiter=platform),
		{"geek_id": " synthetic-geek ", "job_id": " synthetic-job ", "security_id": ""},
		{},
	)

	# 空串 security_id 与 CLI 的空 --security-id 同义：按缺失传给平台。
	assert platform.calls == [("view_geek", " synthetic-geek ", " synthetic-job ", None)]


@pytest.mark.parametrize("value", [123, True, 1.5, ["sid"], {"a": 1}])
def test_recruiter_resume_rejects_non_string_security_id(tmp_path: Path, value: object) -> None:
	"""非字符串 security_id 不得转成 repr 后进入 effect: unknown 请求（Issue #2633）。"""
	from boss_agent_cli.wizard.models import WorkflowInputError

	platform = _FakeRecruiterPlatform()

	with pytest.raises(WorkflowInputError, match="security_id 必须是字符串"):
		DEFAULT_ACTIONS["recruiter_resume"](
			_context(tmp_path, recruiter=platform),
			{"geek_id": "synthetic-geek", "job_id": "synthetic-job", "security_id": value},
			{},
		)

	assert platform.calls == []


@pytest.mark.parametrize("inputs", [{}, {"security_id": None}, {"security_id": "synthetic-sid"}])
def test_recruiter_resume_passes_string_or_absent_security_id(tmp_path: Path, inputs: dict[str, object]) -> None:
	platform = _FakeRecruiterPlatform()

	DEFAULT_ACTIONS["recruiter_resume"](
		_context(tmp_path, recruiter=platform),
		{"geek_id": "synthetic-geek", "job_id": "synthetic-job", **inputs},
		{},
	)

	assert platform.calls == [("view_geek", "synthetic-geek", "synthetic-job", inputs.get("security_id"))]
