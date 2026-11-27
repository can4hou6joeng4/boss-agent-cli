"""Issue #3042：管线不能把缺失 / 非列表的 jobList 当成平台确认的空页。

`boss export --welfare` / `--active` 走 ``run_search_pipeline``；无筛选快路径
（#2932）已区分「显式空列表」与「缺失 / 漂移容器」，管线必须同口径：只有显式
空列表才算列表结束，缺容器时按平台布尔 ``hasMore`` 继续或停止，预算耗尽报截断。
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _job(job_id: str = "job-2") -> dict[str, Any]:
	return {
		"encryptJobId": job_id,
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"welfareList": ["双休"],
		"jobDegree": "本科",
		"jobExperience": "3-5年",
		"salaryDesc": "20K",
		"cityName": "广州",
		"securityId": f"sec-{job_id}",
	}


def _invoke(tmp_path, pages: list[dict[str, Any]], *args: str) -> tuple[dict[str, Any], list[int]]:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.name = "zhipin"
	platform.unwrap_data.side_effect = lambda response: response.get("zpData")
	platform.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	calls: list[int] = []

	def search_jobs(query: str, **kwargs: Any) -> dict[str, Any]:
		page = kwargs.get("page", 1)
		calls.append(page)
		index = min(page, len(pages)) - 1
		return {"code": 0, "zpData": pages[index]}

	platform.search_jobs.side_effect = search_jobs
	# 管线按生产节流每页等待 5–10 秒；这里只验证分页证据，禁用预算等待。
	with patch("boss_agent_cli.commands.export.AuthManager"), patch(
		"boss_agent_cli.commands.export.get_platform_instance", return_value=platform,
	), patch("boss_agent_cli.commands.export.CrawlBudget", return_value=None):
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "export", "golang", *args],
		)
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout), calls


def test_pipeline_continues_past_page_without_job_list(tmp_path):
	"""缺 jobList 且 hasMore=true：继续翻页，后续页的记录必须导出（Issue #3042）。"""
	envelope, calls = _invoke(
		tmp_path,
		[{"hasMore": True}, {"hasMore": False, "jobList": [_job()]}],
		"--welfare", "双休", "--count", "5",
	)
	data = envelope["data"]
	assert calls == [1, 2]
	assert data["count"] == 1
	assert data["stop_reason"] == "platform_has_more_false"
	assert data["truncated"] is False


def test_pipeline_missing_container_with_explicit_false_is_complete(tmp_path):
	"""缺 jobList 但平台明确 hasMore=false：按平台事实结束且报完整。"""
	envelope, calls = _invoke(tmp_path, [{"hasMore": False}], "--welfare", "双休", "--count", "5")
	data = envelope["data"]
	assert calls == [1]
	assert data["count"] == 0
	assert data["stop_reason"] == "platform_has_more_false"
	assert data["truncated"] is False


def test_pipeline_missing_container_never_proving_end_is_truncated(tmp_path):
	"""每页都缺 jobList 且始终 hasMore=true：翻满预算后必须报截断，不能报完整。"""
	envelope, calls = _invoke(tmp_path, [{"hasMore": True}], "--welfare", "双休", "--count", "3")
	data = envelope["data"]
	assert len(calls) > 1
	assert data["count"] == 0
	assert data["stop_reason"] == "page_budget_exhausted"
	assert data["truncated"] is True


def test_pipeline_explicit_empty_job_list_still_ends_scan(tmp_path):
	"""显式空列表仍是平台确认的空页，既有契约不变（#2932 / #2657）。"""
	envelope, calls = _invoke(
		tmp_path, [{"hasMore": True, "jobList": []}], "--welfare", "双休", "--count", "5",
	)
	data = envelope["data"]
	assert calls == [1]
	assert data["stop_reason"] == "empty_page"
	assert data["truncated"] is False


def test_pipeline_null_job_list_is_not_a_complete_empty_page(tmp_path):
	"""``jobList: null`` 与缺失同口径：不能证明列表结束。"""
	envelope, calls = _invoke(
		tmp_path,
		[{"hasMore": True, "jobList": None}, {"hasMore": False, "jobList": [_job()]}],
		"--welfare", "双休", "--count", "5",
	)
	assert calls == [1, 2]
	assert envelope["data"]["count"] == 1
