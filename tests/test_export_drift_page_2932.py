"""Issue #2932：元素全漂移的 jobList 不能被当成平台确认的空页。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls: Any) -> Any:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.name = "zhipin"
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _job(name: str = "后端工程师") -> dict[str, Any]:
	return {
		"encryptJobId": "j-demo",
		"jobName": name,
		"brandName": "演示公司",
		"salaryDesc": "20K",
		"cityName": "上海",
		"jobExperience": "3年",
		"jobDegree": "本科",
		"securityId": "s-demo",
	}


def _invoke(tmp_path: Any, mock_client: Any, count: int = 5) -> dict[str, Any]:
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "export", "演示岗位", "--count", str(count)],
	)
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_all_drifted_page_does_not_hide_later_jobs(mock_auth_cls, mock_client_cls, tmp_path) -> None:
	mock_client = _ctx_mock(mock_client_cls)
	calls: list[int] = []

	def search_jobs(query: str, **kwargs: Any) -> dict[str, Any]:
		page = kwargs.get("page", 1)
		calls.append(page)
		if page == 1:
			return {"zpData": {"hasMore": True, "jobList": [None, 1, "not-a-job"]}}
		return {"zpData": {"hasMore": False, "jobList": [_job()]}}

	mock_client.search_jobs.side_effect = search_jobs

	envelope = _invoke(tmp_path, mock_client)

	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 1
	assert envelope["data"]["stop_reason"] == "platform_has_more_false"
	assert calls == [1, 2]


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_all_drifted_pages_are_reported_as_truncated(mock_auth_cls, mock_client_cls, tmp_path) -> None:
	mock_client = _ctx_mock(mock_client_cls)
	calls: list[int] = []

	def search_jobs(query: str, **kwargs: Any) -> dict[str, Any]:
		calls.append(kwargs.get("page", 1))
		return {"zpData": {"hasMore": True, "jobList": [None, "drifted"]}}

	mock_client.search_jobs.side_effect = search_jobs

	envelope = _invoke(tmp_path, mock_client, count=3)

	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 0
	assert envelope["data"]["stop_reason"] == "page_budget_exhausted"
	assert envelope["data"]["truncated"] is True
	assert len(calls) > 1


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_explicit_empty_job_list_still_stops(mock_auth_cls, mock_client_cls, tmp_path) -> None:
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {"zpData": {"hasMore": True, "jobList": []}}

	envelope = _invoke(tmp_path, mock_client)

	assert envelope["ok"] is True
	assert envelope["data"]["stop_reason"] == "empty_page"
	assert envelope["data"]["truncated"] is False
	assert mock_client.search_jobs.call_count == 1
