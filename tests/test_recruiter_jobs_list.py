"""hr jobs list reads the job-management page. All calls stay offline."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


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


def test_schema_records_jobs_list_page() -> None:
	page = SCHEMA_DATA["commands"]["hr"]["options"]["jobs"]["--page"]
	assert page["type"] == "int"
	assert page["default"] == 1


def test_hr_jobs_mcp_page_is_only_forwarded_for_list() -> None:
	assert _build_args("boss_hr_jobs", {"action": "list", "page": 2}) == ["hr", "jobs", "list", "--page", "2"]
	assert _build_args("boss_hr_jobs", {"action": "offline", "job_id": "j1", "page": 2}) == ["hr", "jobs", "offline", "j1"]
