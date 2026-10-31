"""平台响应里的显式 null：解析不崩溃、不误报，输出字段保持约定的字符串 / 列表类型。

dict.get(key, default) 只在键不存在时用默认值；平台常把缺省字段写成 null
（"jobList": null、"welfareList": null、"bossInfo": null），这类值以前会在 join、
遍历或取子字段时抛 TypeError / AttributeError，再被兜底成 NETWORK_ERROR。
"""

from __future__ import annotations

import importlib
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.models import JobDetail, JobItem, field_dict, field_list, field_text
from boss_agent_cli.main import cli
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.search_filters import SearchFilterCriteria, match_all_welfare
from boss_agent_cli.services.job_card import build_job_from_card

JOB_KEYS = (
	"encryptJobId", "jobName", "brandName", "salaryDesc", "cityName", "areaDistrict", "jobExperience",
	"jobDegree", "skills", "welfareList", "brandIndustry", "brandScaleName", "brandStageName", "bossName",
	"bossTitle", "bossOnline", "securityId", "lid", "jobType", "daysPerWeekDesc", "leastMonthDesc",
	"jobLabels", "activeTimeDesc",
)


def _null_job(**values):
	job = dict.fromkeys(JOB_KEYS)
	job.update(values)
	return job


def _ctx(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (response or {}).get("zpData")
	instance.is_success.side_effect = lambda response: (response or {}).get("code", 0) in (0, 200)
	return instance


def _invoke(module, args, setup):
	command_module = importlib.import_module(f"boss_agent_cli.commands.{module}")
	with patch(f"boss_agent_cli.commands.{module}.get_platform_instance") as platform_cls, \
		patch(f"boss_agent_cli.commands.{module}.AuthManager"), \
		patch.object(command_module, "CacheStore", create=True) as cache_cls:
		cache = _ctx(cache_cls)
		cache.is_greeted.return_value = False
		cache.get_search.return_value = None
		cache.get_job_id.return_value = None
		platform = _ctx(platform_cls)
		setup(platform)
		extra = patch("boss_agent_cli.commands.search.CrawlBudget", return_value=None) if module == "search" else None
		if extra:
			extra.start()
		try:
			result = CliRunner().invoke(cli, ["--json", *args])
		finally:
			if extra:
				extra.stop()
	return result, platform


def _envelope(result):
	payload = json.loads(result.output)
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert payload["ok"] is True, payload["error"]
	assert result.exit_code == 0
	return payload


# ── 归一化函数 ───────────────────────────────────────────────


def test_field_helpers_treat_null_like_missing():
	data = {"text": None, "number": 4, "items": None, "nested": None, "wrong": "x"}
	assert field_text(data, "text") == ""
	assert field_text(data, "text", "离线") == "离线"
	assert field_text(data, "missing", "离线") == "离线"
	assert field_text(data, "number") == "4"
	assert field_list(data, "items") == []
	assert field_list(data, "wrong") == []
	assert field_dict(data, "nested") == {}
	assert field_dict(None, "nested") == {}
	assert field_text(None, "text") == ""


def test_job_item_from_api_keeps_contract_types_for_null_fields():
	item = JobItem.from_api(_null_job()).to_dict()
	for key in ("job_id", "title", "company", "salary", "city", "experience", "education", "security_id", "lid"):
		assert item[key] == "", key
	for key in ("skills", "welfare", "job_labels"):
		assert item[key] == [], key
	assert item["boss_active"] == "离线"
	assert item["job_url"] == ""


def test_job_detail_from_api_survives_null_sections():
	detail = JobDetail.from_api({"jobInfo": None, "bossInfo": None, "brandComInfo": None, "jobDetail": None})
	assert detail.title == ""
	assert detail.boss_active == "离线"
	assert detail.company_info == {"industry": "", "scale": "", "stage": ""}


def test_job_card_and_score_dict_survive_null_fields():
	card = build_job_from_card(dict.fromkeys(("jobName", "jobLabels", "activeTimeDesc", "cityName")), security_id="s1", greeted=False)
	assert card["title"] == "" and card["skills"] == [] and card["boss_active"] == "离线"
	scored = score_job_dict(dict.fromkeys(("title", "city", "skills", "welfare")), criteria=SearchFilterCriteria(query="go", city="杭州"), expect_data=None)
	assert isinstance(scored["match_score"], int)


def test_welfare_match_accepts_empty_list_from_null_field():
	assert match_all_welfare(["双休"], field_list({"welfareList": None}, "welfareList"), "") == []


# ── 命令层：null 列表 / null 字段不再变成 NETWORK_ERROR ─────────────


def _search_returns(job_list):
	def setup(platform):
		platform.search_jobs.return_value = {"code": 0, "zpData": {"jobList": job_list, "hasMore": False}}
		platform.job_detail.return_value = {"code": 0, "zpData": {"jobInfo": None}}
		platform.job_card.return_value = {"code": 0, "zpData": {"jobCard": None}}
	return setup


@pytest.mark.parametrize(
	"args",
	[
		["search", "golang"],
		["search", "golang", "--welfare", "双休"],
		["search", "golang", "--with-score"],
		["search", "golang", "--city", "杭州", "--salary", "10-20K", "--experience", "3-5年"],
	],
)
def test_search_with_null_job_fields_returns_envelope(args):
	result, _ = _invoke("search", args, _search_returns([_null_job(encryptJobId="j1", securityId="s1")]))
	payload = _envelope(result)
	for item in payload["data"]:
		assert isinstance(item["title"], str) and isinstance(item["welfare"], list)


@pytest.mark.parametrize(
	("module", "args", "method", "payload"),
	[
		("search", ["search", "golang"], "search_jobs", {"jobList": None, "hasMore": False}),
		("recommend", ["recommend"], "recommend_jobs", {"jobList": None, "hasMore": False}),
		("history", ["history"], "job_history", {"jobList": None, "hasMore": None}),
		("interviews", ["interviews"], "interview_data", {"interviewList": None}),
	],
)
def test_null_result_list_is_an_empty_result(module, args, method, payload):
	def setup(platform):
		getattr(platform, method).return_value = {"code": 0, "zpData": payload}

	result, _ = _invoke(module, args, setup)
	assert _envelope(result)["data"] == []


@pytest.mark.parametrize("section", ["bossInfo", "brandComInfo"])
def test_detail_null_section_returns_detail_without_browser_fallback(section):
	"""以前这里抛 AttributeError，被吞掉后改走浏览器通道多请求一次，最后误报 JOB_NOT_FOUND。"""
	payload = {
		"jobInfo": {"encryptJobId": "job-1", "jobName": "后端", "securityId": "s1"},
		"bossInfo": {"name": "张"},
		"brandComInfo": {"brandName": "示例"},
		"jobDetail": "描述",
	}
	payload[section] = None

	def setup(platform):
		platform.job_detail.return_value = {"code": 0, "zpData": payload}

	result, platform = _invoke("detail", ["detail", "s1", "--job-id", "job-1"], setup)
	data = _envelope(result)["data"]
	assert data["title"] == "后端"
	assert isinstance(data["boss_name"], str) and isinstance(data["company"], str)
	platform.job_card.assert_not_called()
