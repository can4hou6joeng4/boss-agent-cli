"""招聘者列表 pagination.total 与实际渲染行同源回归（Issue #3034）。"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path, module: str, args: list[str], method: str, payload: dict):
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	getattr(platform, method).return_value = {"zpData": payload}
	with patch(f"boss_agent_cli.commands.recruiter.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.recruiter.{module}.get_recruiter_platform_instance",
		return_value=platform,
	):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	return parsed


def test_applications_total_counts_result_container(tmp_path):
	payload = {
		"result": [{"friendId": 1, "name": "候选人", "jobName": "岗位", "brandName": "公司"}],
		"hasMore": True,
	}
	parsed = _invoke(tmp_path, "applications", ["hr", "applications"], "friend_list", payload)
	assert parsed["pagination"]["total"] == 1
	assert parsed["data"] == payload  # 平台载荷仍原样透传


def test_recommendations_total_counts_items_container(tmp_path):
	payload = {
		"items": [{"name": "候选人", "expectPosition": "岗位"}],
		"hasMore": True,
	}
	parsed = _invoke(
		tmp_path, "recommendations", ["hr", "recommendations", "--job-id", "job_fixture"],
		"recommend_geeks", payload,
	)
	assert parsed["pagination"]["total"] == 1
	assert parsed["data"] == payload


def test_jobs_total_counts_job_list_container(tmp_path):
	payload = {
		"jobList": [{"jobName": "岗位", "jobId": "job_fixture"}],
		"hasMore": True,
	}
	parsed = _invoke(tmp_path, "jobs", ["hr", "jobs", "list"], "list_jobs", payload)
	assert parsed["pagination"]["total"] == 1
	assert parsed["data"] == payload


@pytest.mark.parametrize(
	("module", "args", "method", "preferred", "fallback"),
	[
		("applications", ["hr", "applications"], "friend_list", "friendList", "result"),
		("recommendations", ["hr", "recommendations", "--job-id", "job_fixture"], "recommend_geeks", "geekList", "items"),
		("jobs", ["hr", "jobs", "list"], "list_jobs", "jobList", "items"),
	],
)
def test_empty_preferred_container_does_not_fall_back(tmp_path, module, args, method, preferred, fallback):
	"""空的首选容器是明确的空页，不能读兄弟容器凑出一条总数。"""
	payload = {
		preferred: [],
		fallback: [{"jobName": "岗位", "name": "候选人"}],
		"hasMore": False,
	}
	parsed = _invoke(tmp_path, module, args, method, payload)
	assert parsed["pagination"]["total"] == 0
