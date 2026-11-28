"""批量打招呼的候选预筛与 search / 向导同口径（Issue #3099）。

`batch-greet` 原先只用 `prefilter_platform_job_type` 过滤，城市 / 薪资 / 经验 /
学历不匹配的条目会被收进候选；非 dry-run 时就是向错误职位真实发出招呼。
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


def _job(city: str, job_id: str) -> dict[str, Any]:
	return {
		"encryptJobId": job_id,
		"jobName": "Go 开发",
		"brandName": "演示公司",
		"salaryDesc": "20K-30K",
		"cityName": city,
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"securityId": f"sec_{job_id}",
	}


def _invoke(tmp_path: Path, *args: str) -> dict[str, Any]:
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "batch-greet", "Go 开发", "--dry-run", "--count", "3", *args],
	)
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


@patch("boss_agent_cli.commands.greet.CacheStore")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
def test_batch_greet_drops_items_failing_local_city_prefilter(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, tmp_path
) -> None:
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.search_jobs.return_value = {"zpData": {"jobList": [_job("北京", "j1")]}}
	mock_cache = mock_cache_cls.return_value.__enter__.return_value
	mock_cache.is_greeted.return_value = False

	envelope = _invoke(tmp_path, "--city", "广州")

	assert envelope["ok"] is True
	assert envelope["data"]["dry_run"] is True
	assert envelope["data"]["count"] == 0
	assert envelope["data"]["candidates"] == []
	mock_platform.greet.assert_not_called()


@patch("boss_agent_cli.commands.greet.CacheStore")
@patch("boss_agent_cli.commands.greet.get_platform_instance")
@patch("boss_agent_cli.commands.greet.AuthManager")
def test_batch_greet_keeps_items_matching_local_prefilter(
	mock_auth_cls, mock_platform_cls, mock_cache_cls, tmp_path
) -> None:
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.search_jobs.return_value = {
		"zpData": {"jobList": [_job("北京", "j1"), _job("广州", "j2")]}
	}
	mock_cache = mock_cache_cls.return_value.__enter__.return_value
	mock_cache.is_greeted.return_value = False

	envelope = _invoke(tmp_path, "--city", "广州")

	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 1
	assert [item["city"] for item in envelope["data"]["candidates"]] == ["广州"]
	mock_platform.greet.assert_not_called()
