"""recommend 的分页证据与列表容器解析必须与向导共用实现（Issue #3101）。

- 平台载荷带有效 page 时，CLI 应采信平台页码（索引快照、翻页提示、表格同源）。
- 职位在 recommendList 等替代容器里时，CLI 不能返回空页并覆盖编号索引。

全部离线：平台、登录态与缓存都是 mock。
"""

from __future__ import annotations

import json
from pathlib import Path
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


def _job(suffix: str) -> dict[str, Any]:
	return {
		"encryptJobId": f"job-{suffix}",
		"securityId": f"sid-{suffix}",
		"lid": f"lid-{suffix}",
		"jobName": f"职位{suffix}",
	}


def _invoke(tmp_path: Path, *args: str) -> dict[str, Any]:
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "recommend", *args],
	)
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_uses_platform_corrected_page_everywhere(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, tmp_path
) -> None:
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.recommend_jobs.return_value = {
		"code": 0,
		"zpData": {"page": 7, "hasMore": True, "jobList": [_job("a")]},
	}
	mock_cache_cls.return_value.__enter__.return_value.is_greeted.return_value = False

	envelope = _invoke(tmp_path, "--page", "2")

	assert envelope["ok"] is True
	pagination = envelope["pagination"]
	assert pagination["page"] == 7
	assert pagination["total"] == 1
	assert pagination["index_snapshot"]["page"] == 7
	assert any("recommend --page 8" in action for action in envelope["hints"]["next_actions"])
	mock_platform.recommend_jobs.assert_called_once_with(page=2)


@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_keeps_with_score_on_corrected_next_page(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, tmp_path
) -> None:
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.resume_expect.return_value = {"code": 0, "zpData": {}}
	mock_platform.recommend_jobs.return_value = {
		"code": 0,
		"zpData": {"page": 4, "hasMore": True, "jobList": [_job("a")]},
	}
	mock_cache_cls.return_value.__enter__.return_value.is_greeted.return_value = False

	envelope = _invoke(tmp_path, "--page", "2", "--with-score")

	assert envelope["pagination"]["page"] == 4
	assert any(
		"recommend --page 5 --with-score" in action for action in envelope["hints"]["next_actions"]
	)


@patch("boss_agent_cli.commands.recommend.CacheStore")
@patch("boss_agent_cli.commands.recommend.get_platform_instance")
@patch("boss_agent_cli.commands.recommend.AuthManager")
def test_recommend_reads_alternate_list_container(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, tmp_path
) -> None:
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.recommend_jobs.return_value = {
		"code": 0,
		"zpData": {"recommendList": [_job("x")], "hasMore": False},
	}
	mock_cache_cls.return_value.__enter__.return_value.is_greeted.return_value = False

	envelope = _invoke(tmp_path)

	assert envelope["ok"] is True
	assert len(envelope["data"]) == 1
	assert envelope["data"][0]["job_id"] == "job-x"
	assert envelope["pagination"]["total"] == 1
	assert envelope["pagination"]["index_snapshot"]["count"] == 1
	assert envelope["pagination"]["has_more"] is False
