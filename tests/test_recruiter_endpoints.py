"""Verify recruiter.yaml loads and produces expected endpoint constants."""
from boss_agent_cli.api.recruiter_endpoints import (
	BASE_URL,
	BOSS_FRIEND_LIST_URL,
	BOSS_GREET_LIST_URL,
	BOSS_SEARCH_GEEK_URL,
	BOSS_VIEW_GEEK_URL,
	BOSS_JOB_LIST_URL,
	BOSS_JOB_OFFLINE_URL,
	BOSS_SEND_MESSAGE_URL,
	CODE_SUCCESS,
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
