from boss_agent_cli.api.endpoints import (
	CITY_CODES, SALARY_CODES, EXPERIENCE_CODES,
	JOB_TYPE_CODES,
	CODE_ACCOUNT_RISK, CODE_STOKEN_EXPIRED, CODE_RATE_LIMITED,
	USER_INFO_URL,
)
from boss_agent_cli.api.models import JobDetail, JobItem

import pytest


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


def test_environment_risk_code_37_stops_without_refresh_or_retry():
	"""环境风险 code 37 必须立即抛 EnvironmentRiskError：不刷新、不重试。"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient, EnvironmentRiskError

	auth = MagicMock()
	client = BossClient(auth)
	mock_browser = MagicMock()
	mock_browser.request.return_value = {"code": 37, "message": "您的环境存在异常"}
	mock_browser._is_cdp = True
	client._browser_session = mock_browser

	with pytest.raises(EnvironmentRiskError) as exc_info:
		client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")

	assert exc_info.value.is_cdp is True
	mock_browser.request.assert_called_once()
	auth.force_refresh.assert_not_called()
	client.close()


def test_ambiguous_code_37_fails_closed_as_environment_risk():
	"""语义不明的 code 37 同样 fail closed，绝不刷新。"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient, EnvironmentRiskError

	auth = MagicMock()
	client = BossClient(auth)
	mock_browser = MagicMock()
	mock_browser.request.return_value = {"code": 37, "message": "请求失败"}
	client._browser_session = mock_browser

	with pytest.raises(EnvironmentRiskError):
		client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")

	mock_browser.request.assert_called_once()
	auth.force_refresh.assert_not_called()
	client.close()


def test_explicit_token_code_37_is_returned_without_refresh_or_retry():
	"""浏览器通道：即便文案明确指向 token 过期，也不刷新、不重试，原样返回 code 37 交上层 parse_error。

	浏览器通道的 stoken 由页面 JS 生成，force_refresh 拿不到新凭证，重试只是对已被风控
	拦截的高风险端点再打一次（docs/research/platforms/zhipin.md 红线）。token_expired 的
	code 37 在此原样返回响应字典，由上层 parse_error 映射成 TOKEN_REFRESH_FAILED，
	与合并前行为逐字一致；刷新只发生在 httpx 通道。
	"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	client = BossClient(auth, cdp_url="http://127.0.0.1:9222")
	mock_browser = MagicMock()
	mock_browser.request.return_value = {"code": 37, "message": "__zp_stoken__ 已过期"}
	client._browser_session = mock_browser

	result = client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")

	assert result["code"] == 37
	mock_browser.request.assert_called_once()
	auth.force_refresh.assert_not_called()
	client.close()


def test_browser_channel_never_refreshes_on_any_code_37():
	"""红线回归：浏览器通道对任意 code 37（无论分类结果）都不得调用 auth.force_refresh。

	覆盖三种文案形态：明确 token、明确环境风险、语义不明。无论哪种，浏览器通道都只发
	一次请求且绝不刷新——环境风险抛 EnvironmentRiskError，其余原样返回响应字典。
	"""
	from unittest.mock import MagicMock
	from boss_agent_cli.api.client import BossClient, EnvironmentRiskError

	cases = [
		({"code": 37, "message": "__zp_stoken__ 已过期"}, None),  # token_expired → 原样返回
		({"code": 37, "message": "您的环境存在异常"}, EnvironmentRiskError),  # environment_risk → 抛
		({"code": 37, "message": "请求失败"}, EnvironmentRiskError),  # 语义不明 → fail closed 抛
	]
	for payload, expected_exc in cases:
		auth = MagicMock()
		client = BossClient(auth)
		mock_browser = MagicMock()
		mock_browser.request.return_value = payload
		client._browser_session = mock_browser

		if expected_exc is not None:
			with pytest.raises(expected_exc):
				client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")
		else:
			assert client._browser_request("GET", "/wapi/zpgeek/search/joblist.json")["code"] == 37

		mock_browser.request.assert_called_once()
		auth.force_refresh.assert_not_called()
		client.close()


def test_httpx_ambiguous_code_37_returns_dict_without_refresh():
	"""httpx 通道：语义不明的 code 37 不刷新、不 sleep，按响应字典原样返回。"""
	from unittest.mock import MagicMock, patch

	from boss_agent_cli.api.client import BossClient

	auth = MagicMock()
	client = BossClient(auth)
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None
	mock_httpx = MagicMock()
	mock_httpx.request.return_value = MagicMock(
		status_code=200, text="", cookies=MagicMock(),
		json=lambda: {"code": CODE_STOKEN_EXPIRED, "message": "请求失败"},
	)
	mock_httpx.cookies = MagicMock()
	client._client = mock_httpx

	with patch("boss_agent_cli.api._base_client.time.sleep") as mock_sleep:
		data = client._request("GET", USER_INFO_URL)

	assert data["code"] == CODE_STOKEN_EXPIRED
	auth.force_refresh.assert_not_called()
	mock_sleep.assert_not_called()
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
