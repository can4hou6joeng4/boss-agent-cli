"""搜索预筛与详情描述对平台字段漂移的容错（Issue #1335）。"""

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	meets_education_threshold,
	meets_experience_threshold,
	parse_salary_range,
	prefilter_job,
	resolve_welfare_keywords,
	run_search_pipeline,
)


class FakeLogger:
	def __init__(self):
		self.messages: list[str] = []

	def info(self, message: str):
		self.messages.append(message)


class FakeClient:
	name = "zhipin"

	def __init__(self, item: dict, card: dict):
		self.item = item
		self.card = card

	def search_jobs(self, query: str, **filters):
		return {"zpData": {"jobList": [self.item], "hasMore": False}}

	def is_success(self, response: dict) -> bool:
		return True

	def parse_error(self, response: dict) -> tuple[str, str]:
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict):
		return response.get("zpData")

	def job_card(self, security_id: str, lid: str = ""):
		return {"zpData": {"jobCard": self.card}}

	def is_browser_only(self) -> bool:
		return False


def _item(**overrides):
	item = {
		"encryptJobId": "job-1",
		"jobName": "后端开发",
		"brandName": "Synthetic",
		"salaryDesc": "20-30K",
		"cityName": "北京",
		"areaDistrict": "朝阳区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": [],
		"welfareList": [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "HR",
		"bossTitle": "招聘者",
		"bossOnline": False,
		"securityId": "sid-1",
	}
	item.update(overrides)
	return item


@pytest.mark.parametrize("salary", [20000, 20.5, None, ["20-50K"], {"k": 1}])
def test_parse_salary_range_tolerates_non_string(salary):
	assert parse_salary_range(salary) is None


@pytest.mark.parametrize("candidate", [["3-5年"], {"y": 3}, 3, None])
def test_thresholds_treat_non_string_candidate_as_unknown(candidate):
	assert meets_experience_threshold(candidate, "3-5年") is True
	assert meets_education_threshold(candidate, "本科") is True


@pytest.mark.parametrize("city_name", [123, True, ["北京"], {"name": "北京"}])
def test_prefilter_tolerates_non_string_city(city_name):
	passed, reasons = prefilter_job(
		{"cityName": city_name, "salaryDesc": "20-30K"},
		SearchFilterCriteria(query="后端", city="北京"),
	)
	assert passed is True
	assert reasons == []


def test_prefilter_tolerates_non_string_salary_and_thresholds():
	criteria = SearchFilterCriteria(query="后端", salary="20-50K", experience="3-5年", education="本科")
	passed, reasons = prefilter_job(
		{
			"cityName": "北京",
			"salaryDesc": 20000,
			"jobExperience": ["3-5年"],
			"jobDegree": {"name": "本科"},
		},
		criteria,
	)
	assert passed is True
	assert reasons == []


def test_pipeline_list_prefilter_tolerates_field_drift(tmp_path):
	client = FakeClient(_item(cityName=123, salaryDesc=20000), {})
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		result = run_search_pipeline(
			client,
			cache,
			FakeLogger(),
			criteria=SearchFilterCriteria(query="后端", city="北京", salary="20-50K"),
		)
	assert [item["job_id"] for item in result.items] == ["job-1"]


def test_active_detail_tolerates_non_string_post_description(tmp_path):
	"""--active 串行路径：描述字段漂移不能让整次搜索落 NETWORK_ERROR。"""
	client = FakeClient(_item(), {"postDescription": ["双休"], "activeTimeDesc": "3日内活跃"})
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		result = run_search_pipeline(
			client,
			cache,
			FakeLogger(),
			criteria=SearchFilterCriteria(query="后端"),
			active="3d",
		)
		assert [item["job_id"] for item in result.items] == ["job-1"]
		assert cache.get_job_desc("job-1") is None


def test_welfare_thread_pool_tolerates_non_string_post_description(tmp_path):
	"""--welfare 线程池路径：漂移描述按空文本处理，不静默吞掉异常后丢结果。"""
	client = FakeClient(_item(), {"postDescription": {"text": "周末双休"}})
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		result = run_search_pipeline(
			client,
			cache,
			FakeLogger(),
			criteria=SearchFilterCriteria(query="后端"),
			welfare_conditions=[("双休", resolve_welfare_keywords("双休"))],
		)
		assert result.items == []
		assert cache.get_job_desc("job-1") is None
