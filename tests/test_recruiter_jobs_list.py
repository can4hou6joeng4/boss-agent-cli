"""hr jobs list reads the job-management page. All calls stay offline."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def _hint(command: str) -> str:
	"""默认数据目录下的招聘者后继命令。"""
	return f"boss --data-dir {(Path.home() / '.boss-agent').resolve()} {command}"


def _management_page() -> dict:
	return {
		"page": 2,
		"hasMore": False,
		"totalSize": 1,
		"data": [
			{
				"encryptJobId": "j1",
				"encryptId": "e1",
				"jobName": "role",
				"jobStatus": 0,
				"jobAuditStatus": 1,
				"salaryDesc": "1-2K",
			}
		],
	}


def test_list_jobs_requests_management_page() -> None:
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(return_value={"code": 0, "zpData": _management_page()})

	result = client.list_jobs(page=2)

	assert result["zpData"]["data"][0]["jobName"] == "role"
	client._request.assert_called_once_with(
		"GET",
		ep.BOSS_JOB_LIST_URL,
		params={"page": 2},
	)
	assert ep.BOSS_JOB_LIST_URL.endswith("/wapi/zpjob/job/data/list")


def test_list_jobs_defaults_to_first_page() -> None:
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(return_value={"code": 0, "zpData": {}})

	client.list_jobs()

	assert client._request.call_args.kwargs["params"] == {"page": 1}


def test_hr_jobs_list_returns_unwrapped_management_payload() -> None:
	client = MagicMock()
	page = _management_page()
	client.list_jobs.return_value = {"code": 0, "zpData": page}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "jobs", "list", "--page", "2"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["command"] == "recruiter-jobs-list"
	assert payload["data"] == page
	client.list_jobs.assert_called_once_with(page=2)


def test_hr_jobs_list_defaults_page_to_one() -> None:
	client = MagicMock()
	client.list_jobs.return_value = {
		"code": 0,
		"zpData": {"page": 1, "hasMore": False, "totalSize": 0, "data": []},
	}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "jobs", "list"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["command"] == "recruiter-jobs-list"
	assert payload["data"]["page"] == 1
	assert payload["data"]["data"] == []
	client.list_jobs.assert_called_once_with(page=1)


@pytest.mark.parametrize(("platform_has_more", "expected"), [(True, True), (False, False), ("missing", None)])
def test_hr_jobs_list_reports_pagination_evidence(platform_has_more: object, expected: bool | None) -> None:
	client = MagicMock()
	page = _management_page()
	if platform_has_more == "missing":
		page.pop("hasMore")
	else:
		page["hasMore"] = platform_has_more
	client.list_jobs.return_value = {"code": 0, "zpData": page}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "jobs", "list", "--page", "2"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	pagination = payload["pagination"]
	assert pagination["page"] == 2
	assert pagination["has_more"] is expected
	assert pagination["has_more_source"] == ("platform_hasMore" if expected is not None else "unknown")
	assert pagination["total"] == 1
	assert pagination["total_kind"] == "returned_page_count"
	assert pagination["total_available"] == 1
	next_pages = [action for action in payload["hints"]["next_actions"] if "hr jobs list --page" in action]
	if expected is True:
		assert next_pages == [_hint("hr jobs list --page 3")]
		assert "operator_actions" not in payload["hints"]
	else:
		assert next_pages == []
		if expected is None:
			assert payload["hints"]["operator_actions"] == [
				"平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有职位"
			]
		else:
			assert "operator_actions" not in payload["hints"]
	client.list_jobs.assert_called_once_with(page=2)


def test_hr_jobs_list_ignores_malformed_pagination_fields() -> None:
	client = MagicMock()
	client.list_jobs.return_value = {
		"code": 0,
		"zpData": {"page": "3", "hasMore": "yes", "totalSize": -1, "data": []},
	}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "jobs", "list", "--page", "5"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["pagination"] == {
		"page": 5,
		"has_more": None,
		"has_more_source": "unknown",
		"total": 0,
		"total_kind": "returned_page_count",
		"total_available": None,
	}


def test_hr_jobs_list_keeps_envelope_when_payload_is_not_a_mapping() -> None:
	client = MagicMock()
	client.list_jobs.return_value = {"code": 0, "zpData": ["unexpected"]}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "jobs", "list", "--page", "4"])

	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["data"] == ["unexpected"]
	assert payload["pagination"] == {
		"page": 4,
		"has_more": None,
		"has_more_source": "unknown",
		"total": None,
		"total_kind": "returned_page_count",
		"total_available": None,
	}


def test_schema_records_jobs_list_page() -> None:
	page = SCHEMA_DATA["commands"]["hr"]["options"]["jobs"]["list"]["--page"]
	assert page["type"] == "int"
	assert page["default"] == 1


def test_hr_jobs_mcp_page_is_only_forwarded_for_list() -> None:
	assert _build_args("boss_hr_jobs", {"action": "list", "page": 2}) == ["hr", "jobs", "list", "--page", "2"]
	assert _build_args("boss_hr_jobs", {"action": "offline", "job_id": "j1", "page": 2}) == ["hr", "jobs", "offline", "j1"]
