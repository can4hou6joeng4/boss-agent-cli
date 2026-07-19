from boss_agent_cli.api.endpoints import (
	CITY_CODES, SALARY_CODES, EXPERIENCE_CODES,
	JOB_TYPE_CODES,
	CODE_ACCOUNT_RISK, CODE_STOKEN_EXPIRED, CODE_RATE_LIMITED,
)
from boss_agent_cli.api.models import JobDetail, JobItem


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


def test_job_type_code_lookup_distinguishes_internship_from_part_time():
	assert JOB_TYPE_CODES["全职"] == "1901"
	assert JOB_TYPE_CODES["实习"] == "1902"
	assert JOB_TYPE_CODES["兼职"] == "1903"
	assert len(set(JOB_TYPE_CODES.values())) == 3


def test_response_code_constants():
	"""验证 BOSS API 返回码常量加载正确（含风控 code 36）"""
	assert CODE_STOKEN_EXPIRED == 37
	assert CODE_RATE_LIMITED == 9
	assert CODE_ACCOUNT_RISK == 36


def test_zhilian_spec_loads_from_yaml_and_is_cached():
	"""智联端点必须从 zhilian.yaml 加载并缓存。"""
	from boss_agent_cli.api.endpoints_loader import get_zhilian_spec

	spec = get_zhilian_spec()
	assert spec is get_zhilian_spec()
	assert spec.base_url == "https://www.zhaopin.com"
	assert spec.response_codes["success"] == 200
	assert spec.endpoints["search"].url == "https://fe-api.zhaopin.com/api/c/salesman-search/v2"
	assert spec.endpoints["detail"].url.endswith("/api/c/jobs/{job_id}/info")
	assert spec.endpoints["user_info"].referer == "https://i.zhaopin.com/"


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
		"jobType": 4,
		"daysPerWeekDesc": "4天/周",
		"leastMonthDesc": "3个月",
		"jobLabels": ["4天/周", "3个月", "本科"],
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
	assert job.raw_job_type == 4
	assert job.employment_type == "实习"
	assert job.days_per_week == "4天/周"
	assert job.least_month == "3个月"
	assert job.job_labels == ["4天/周", "3个月", "本科"]


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


def test_job_detail_preserves_internship_metadata():
	raw = {
		"jobInfo": {
			"encryptJobId": "job_intern",
			"jobName": "产品实习生",
			"securityId": "sec_intern",
			"jobType": 4,
			"daysPerWeekDesc": "5天/周",
			"leastMonthDesc": "3个月",
			"payTypeDesc": "按天结算",
		},
		"bossInfo": {},
		"brandComInfo": {},
	}

	detail = JobDetail.from_api(raw)
	data = detail.to_dict()

	assert data["raw_job_type"] == 4
	assert data["employment_type"] == "实习"
	assert data["days_per_week"] == "5天/周"
	assert data["least_month"] == "3个月"
	assert data["pay_type"] == "按天结算"


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


def test_job_card_httpx_returns_result():
	"""job_card_httpx 成功时返回 httpx 通道结果。"""
	from unittest.mock import MagicMock, patch
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	expected = {"code": 0, "zpData": {"jobCard": {"jobName": "Go工程师"}}}
	with patch.object(client, "_request", return_value=expected) as mock_req:
		result = client.job_card_httpx("sec123", lid="lid1")
	assert result == expected
	mock_req.assert_called_once()
	client.close()


def test_job_card_with_httpx_fallback():
	"""job_card 先尝试 httpx，失败后降级到浏览器通道。"""
	from unittest.mock import MagicMock, patch
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	browser_result = {"code": 0, "zpData": {"jobCard": {"jobName": "浏览器结果"}}}
	with patch.object(client, "job_card_httpx", side_effect=Exception("httpx failed")), \
		patch.object(client, "_browser_request", return_value=browser_result) as mock_browser:
		result = client.job_card("sec123", lid="lid1")
	assert result == browser_result
	mock_browser.assert_called_once()
	client.close()


def test_resume_status_calls_request():
	"""resume_status 应通过 httpx 通道请求。"""
	from unittest.mock import MagicMock, patch
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	expected = {"code": 0, "zpData": {"resumeStatus": {"completeness": 80}}}
	with patch.object(client, "_request", return_value=expected) as mock_req:
		result = client.resume_status()
	assert result == expected
	mock_req.assert_called_once()
	client.close()


def test_geek_get_job_calls_request():
	"""geek_get_job 应通过 httpx 通道请求。"""
	from unittest.mock import MagicMock, patch
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {}, "user_agent": "ua", "stoken": "s"}
	client = BossClient(auth)

	expected = {"code": 0, "zpData": {"hasJob": True}}
	with patch.object(client, "_request", return_value=expected) as mock_req:
		result = client.geek_get_job("sec123")
	assert result == expected
	mock_req.assert_called_once()
	client.close()
