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


def test_score_job_item_nationwide_search_falls_back_to_expected_city():
	"""全国不是具体城市：不能给每个职位都记「城市不匹配」，城市评分改看求职期望。"""
	job = _job(cityName="上海")
	criteria = SearchFilterCriteria(query="golang", city="全国")

	without_expect = score_job_item(job, criteria=criteria, expect_data=None)
	assert "城市不匹配" not in without_expect["mismatch_reasons"]
	assert "城市匹配" not in without_expect["match_reasons"]

	with_expect = score_job_item(job, criteria=criteria, expect_data={"city": "上海"})
	assert "城市匹配" in with_expect["match_reasons"]


# ── 平台字段与求职期望类型漂移（Issue #1432） ───────────────────


def test_skills_with_non_string_elements_do_not_crash_keyword_match():
	"""skills 数组混入数字时 join 前逐项 str()，关键词匹配仍可用。"""
	job = _job(skills=["Golang", 3, None])
	criteria = SearchFilterCriteria(query="golang")

	result = score_job_item(job, criteria=criteria, expect_data=None)

	assert "关键词直接命中" in result["match_reasons"]


def test_expect_city_list_is_ignored():
	"""resume_expect 的 city / degree 漂移成数组时按「无此维度」处理，不抛 TypeError。"""
	job = _job()
	result = score_job_item(job, criteria=None, expect_data={"city": ["广州"], "degree": ["本科"]})

	assert "城市匹配" not in result["match_reasons"]
	assert "城市不匹配" not in result["mismatch_reasons"]
	assert "学历满足要求" not in result["match_reasons"]
	assert "学历低于要求" not in result["mismatch_reasons"]
	assert result["match_score"] >= 0


def test_expect_scalar_int_is_ignored_without_crash():
	job = _job()
	result = score_job_item(job, criteria=None, expect_data={"city": 123, "salary": 42, "degree": None})
	assert isinstance(result["match_score"], int)


def test_meets_education_threshold_tolerates_non_string_required():
	"""期望学历漂移成数组时，字典查表不再抛 unhashable type。"""
	from boss_agent_cli.search_filters import meets_education_threshold, meets_experience_threshold

	assert meets_education_threshold("本科", ["本科"]) is True
	assert meets_experience_threshold("3-5年", ["3-5年"]) is True


def test_expect_preferences_fall_back_when_primary_keys_drift_to_containers():
	"""主键是真值容器时不能吞掉合法的 camelCase 回退键（Issue #2740）。"""
	job = _job(welfareList=[])
	expect = {
		"city": ["广州"], "cityName": "广州",
		"salary": ["20-30K"], "salaryDesc": "20-30K",
		"degree": ["本科"], "education": "本科",
	}
	result = score_job_item(job, criteria=None, expect_data=expect)

	assert result["match_score"] == 65
	assert result["match_reasons"] == ["城市匹配", "薪资满足预期", "学历满足要求"]
