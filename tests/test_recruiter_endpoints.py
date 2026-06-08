"""Verify recruiter.yaml loads and produces expected endpoint constants."""
from boss_agent_cli.api.endpoints_loader import get_recruiter_spec
from boss_agent_cli.api.recruiter_endpoints import (
	BASE_URL,
	BOSS_FRIEND_LIST_URL,
	BOSS_GREET_LIST_URL,
	BOSS_SEARCH_GEEK_URL,
	BOSS_VIEW_GEEK_URL,
	BOSS_JOB_LIST_URL,
	BOSS_JOB_OFFLINE_URL,
	BOSS_SEND_MESSAGE_URL,
	CODE_ACCOUNT_RISK,
	CODE_RATE_LIMITED,
	CODE_STOKEN_EXPIRED,
	CODE_SUCCESS,
	DEFAULT_HEADERS,
	REFERER_MAP,
)


def test_recruiter_base_url():
	assert BASE_URL == "https://www.zhipin.com"


def test_recruiter_urls_are_absolute():
	for url in [
		BOSS_FRIEND_LIST_URL,
		BOSS_GREET_LIST_URL,
		BOSS_SEARCH_GEEK_URL,
		BOSS_VIEW_GEEK_URL,
		BOSS_JOB_LIST_URL,
		BOSS_JOB_OFFLINE_URL,
		BOSS_SEND_MESSAGE_URL,
	]:
		assert url.startswith("https://www.zhipin.com/wapi/")


def test_recruiter_response_codes():
	assert CODE_SUCCESS == 0



def test_recruiter_response_code_contracts_include_retryable_failures():
	assert {
		"success": CODE_SUCCESS,
		"stoken_expired": CODE_STOKEN_EXPIRED,
		"rate_limited": CODE_RATE_LIMITED,
		"account_risk": CODE_ACCOUNT_RISK,
	} == {
		"success": 0,
		"stoken_expired": 37,
		"rate_limited": 9,
		"account_risk": 36,
	}


def test_recruiter_spec_keeps_named_endpoint_methods_and_urls():
	spec = get_recruiter_spec()

	assert spec.endpoints["boss_friend_list"].method == "POST"
	assert spec.endpoints["boss_friend_list"].url == BOSS_FRIEND_LIST_URL
	assert spec.endpoints["boss_search_geek"].method == "GET"
	assert spec.endpoints["boss_search_geek"].url == BOSS_SEARCH_GEEK_URL
	assert spec.endpoints["boss_send_message"].method == "POST"
	assert spec.endpoints["boss_send_message"].url == BOSS_SEND_MESSAGE_URL


def test_recruiter_endpoint_urls_are_unique_and_referers_are_mapped():
	spec = get_recruiter_spec()
	urls = [endpoint.url for endpoint in spec.endpoints.values()]

	assert len(urls) == len(set(urls))
	assert set(REFERER_MAP) == set(urls)
	assert REFERER_MAP[BOSS_SEARCH_GEEK_URL] == "https://www.zhipin.com/web/frame/search/"
	assert REFERER_MAP[BOSS_SEND_MESSAGE_URL] == "https://www.zhipin.com/web/chat/index"


def test_recruiter_default_headers_include_browser_origin_contract():
	assert DEFAULT_HEADERS["Origin"] == BASE_URL
	assert DEFAULT_HEADERS["Referer"] == f"{BASE_URL}/"
	assert DEFAULT_HEADERS["Accept"] == "application/json, text/plain, */*"
	assert DEFAULT_HEADERS["Sec-Fetch-Site"] == "same-origin"
	assert DEFAULT_HEADERS["sec-ch-ua-platform"] == '"macOS"'
