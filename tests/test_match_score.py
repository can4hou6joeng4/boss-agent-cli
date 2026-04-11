from boss_agent_cli.api.models import JobItem
from boss_agent_cli.match_score import score_job_item
from boss_agent_cli.search_filters import SearchFilterCriteria


def _job(**overrides):
	raw = {
		"encryptJobId": "job_001",
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"salaryDesc": "20-30K",
		"cityName": "广州",
		"areaDistrict": "天河区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang"],
		"welfareList": ["双休", "五险一金"],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": "sec_001",
	}
	raw.update(overrides)
	return JobItem.from_api(raw)


def test_score_job_item_prefers_exact_search_constraints():
	job = _job()
	criteria = SearchFilterCriteria(
		query="golang",
		city="广州",
		salary="20-50K",
		experience="3-5年",
		education="本科",
	)
	result = score_job_item(job, criteria=criteria, expect_data=None)
	assert result["match_score"] >= 80
	assert "城市匹配" in result["match_reasons"]
	assert "薪资满足预期" in result["match_reasons"]


def test_score_job_item_penalizes_city_and_salary_mismatch():
	job = _job(cityName="上海", salaryDesc="8-12K")
	criteria = SearchFilterCriteria(query="golang", city="广州", salary="20-50K")
	result = score_job_item(job, criteria=criteria, expect_data=None)
	assert result["match_score"] < 60
	assert "城市不匹配" in result["mismatch_reasons"]
	assert "薪资低于预期" in result["mismatch_reasons"]


def test_score_job_item_can_use_expect_data_when_criteria_missing():
	job = _job()
	expect_data = {"city": "广州", "salary": "20-50K", "degree": "本科"}
	result = score_job_item(job, criteria=None, expect_data=expect_data)
	assert result["match_score"] >= 60
	assert result["match_reasons"]
