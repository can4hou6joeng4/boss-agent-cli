"""向导招聘者 recommendations goal：推荐牛人读取（Issue #2682）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import GOALS, catalog_data
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


class _RecruiterPlatform:
	def __init__(self, payload: Any = None, code: int = 0) -> None:
		self._payload = (
			{"hasMore": True, "geekList": [{"geekId": "g1", "securityId": "s1"}]} if payload is None else payload
		)
		self._code = code
		self.calls: list[tuple[str, int]] = []

	def __enter__(self) -> "_RecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def recommend_geeks(self, job_id: str, page: int = 1) -> dict[str, Any]:
		self.calls.append((job_id, page))
		if self._code != 0:
			return {"code": self._code, "msg": "boom"}
		return {"code": 0, "zpData": self._payload}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


def _context(tmp_path, platform: _RecruiterPlatform, constructed: list[int] | None = None) -> ActionContext:
	def factory(*args: Any) -> _RecruiterPlatform:
		if constructed is not None:
			constructed.append(len(constructed) + 1)
		return platform

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger(),
		recruiter_factory=factory,
	)


def _run(context: ActionContext, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS["recruiter_recommendations"](context, inputs, {}).data)


def test_reads_recommendations_with_pagination(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_context(tmp_path, platform), {"job_id": "job-1", "page": 2})

	assert platform.calls == [("job-1", 2)]
	assert data["items"][0]["geekId"] == "g1"
	assert data["pagination"] == {
		"page": 2,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}


def test_default_page_is_one(tmp_path):
	platform = _RecruiterPlatform()

	_run(_context(tmp_path, platform), {"job_id": "job-1"})

	assert platform.calls == [("job-1", 1)]


@pytest.mark.parametrize("job_id", [None, ""])
def test_missing_job_id_is_rejected_before_platform(tmp_path, job_id):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowActionError) as caught:
		_run(_context(tmp_path, platform, constructed), {} if job_id is None else {"job_id": job_id})

	assert caught.value.code == "WIZARD_INPUT_REQUIRED"
	assert constructed == []
	assert platform.calls == []


@pytest.mark.parametrize("job_id", ["   ", "\t"])
def test_blank_job_id_is_rejected_before_platform(tmp_path, job_id):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowActionError) as caught:
		_run(_context(tmp_path, platform, constructed), {"job_id": job_id})

	assert caught.value.code == "INVALID_PARAM"
	assert constructed == []
	assert platform.calls == []


@pytest.mark.parametrize("page", [0, -1, "x", True, 1.5])
def test_invalid_page_is_rejected_before_platform(tmp_path, page):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowInputError):
		_run(_context(tmp_path, platform, constructed), {"job_id": "job-1", "page": page})

	assert constructed == []
	assert platform.calls == []


def test_platform_failure_maps_to_workflow_error(tmp_path):
	platform = _RecruiterPlatform(code=500)

	with pytest.raises(WorkflowActionError) as caught:
		_run(_context(tmp_path, platform), {"job_id": "job-1"})

	assert caught.value.code == "NETWORK_ERROR"


def test_catalog_exposes_recruiter_recommendations_goal():
	goal = GOALS["recruiter"]["recommendations"]

	assert goal.role == "recruiter"
	assert goal.required_inputs == ("job_id",)
	assert goal.steps == ("auth_status", "recruiter_recommendations")
	assert "recommendations" in catalog_data()["roles"]["recruiter"]["goals"]
