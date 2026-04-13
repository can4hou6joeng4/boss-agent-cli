from boss_agent_cli.api.endpoints import (
	CITY_CODES, SALARY_CODES, EXPERIENCE_CODES,
	CODE_ACCOUNT_RISK, CODE_STOKEN_EXPIRED, CODE_RATE_LIMITED,
)
from boss_agent_cli.api.models import JobItem


def test_city_code_lookup():
	assert CITY_CODES["北京"] == "101010100"
	assert CITY_CODES["杭州"] == "101210100"
	assert "火星" not in CITY_CODES


def test_salary_code_lookup():
	assert SALARY_CODES["10-20K"] == "405"
	assert SALARY_CODES["20-50K"] == "406"


def test_experience_code_lookup():
	assert EXPERIENCE_CODES["应届"] == "108"
	assert EXPERIENCE_CODES["3-5年"] == "104"


def test_response_code_constants():
	"""验证 BOSS API 返回码常量加载正确（含风控 code 36）"""
	assert CODE_STOKEN_EXPIRED == 37
	assert CODE_RATE_LIMITED == 9
	assert CODE_ACCOUNT_RISK == 36


def test_job_item_from_api():
	raw = {
		"encryptJobId": "abc123",
		"jobName": "Golang 工程师",
		"brandName": "字节跳动",
		"salaryDesc": "25-50K·15薪",
		"cityName": "北京",
		"areaDistrict": "海淀区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang", "Gin"],
		"welfareList": ["五险一金", "双休"],
		"brandIndustry": "互联网",
		"brandScaleName": "10000人以上",
		"brandStageName": "已上市",
		"bossName": "张先生",
		"bossTitle": "技术总监",
		"bossOnline": True,
		"securityId": "sec_xxx",
	}
	job = JobItem.from_api(raw)
	assert job.job_id == "abc123"
	assert job.title == "Golang 工程师"
	assert job.company == "字节跳动"
	assert job.security_id == "sec_xxx"
	assert job.district == "海淀区"
	assert job.skills == ["Golang", "Gin"]
	assert "双休" in job.welfare
	assert job.industry == "互联网"
	assert job.scale == "10000人以上"


def test_job_item_to_dict():
	raw = {
		"encryptJobId": "abc123",
		"jobName": "Golang 工程师",
		"brandName": "字节跳动",
		"salaryDesc": "25-50K",
		"cityName": "北京",
		"areaDistrict": "朝阳区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang"],
		"welfareList": ["五险一金"],
		"brandIndustry": "互联网",
		"brandScaleName": "10000人以上",
		"brandStageName": "已上市",
		"bossName": "张先生",
		"bossTitle": "CTO",
		"bossOnline": False,
		"securityId": "sec_001",
	}
	job = JobItem.from_api(raw)
	d = job.to_dict()
	assert d["job_id"] == "abc123"
	assert d["boss_active"] == "离线"
	assert d["greeted"] is False
	assert d["welfare"] == ["五险一金"]
	assert d["skills"] == ["Golang"]


def test_account_risk_error_raised_on_code_36():
	"""_browser_request 收到 code 36 时应抛出 AccountRiskError"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient, AccountRiskError

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	# 模拟 browser session 返回 code 36
	mock_browser = MagicMock()
	mock_browser.request.return_value = {
		"code": 36,
		"message": "您的账户存在异常行为.",
		"zpData": {},
	}
	mock_browser._is_cdp = False
	mock_browser._is_bridge = False
	client._browser_session = mock_browser

	import pytest
	with pytest.raises(AccountRiskError) as exc_info:
		client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")

	assert "code 36" in str(exc_info.value)
	assert "风控拦截" in str(exc_info.value)
	assert exc_info.value.is_cdp is False
	client.close()


def test_account_risk_error_not_raised_on_success():
	"""_browser_request 收到 code 0 时正常返回"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	mock_browser = MagicMock()
	mock_browser.request.return_value = {
		"code": 0,
		"message": "Success",
		"zpData": {"jobList": [{"jobName": "test"}]},
	}
	client._browser_session = mock_browser

	result = client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")
	assert result["code"] == 0
	assert result["zpData"]["jobList"][0]["jobName"] == "test"
	client.close()
