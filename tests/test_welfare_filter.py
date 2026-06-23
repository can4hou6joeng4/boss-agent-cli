import json
from unittest.mock import patch

from click.testing import CliRunner
from boss_agent_cli.main import cli


def _make_job_raw(job_id="j1", welfare=None, security_id="sec_1"):
	return {
		"encryptJobId": job_id,
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"salaryDesc": "20K",
		"cityName": "广州",
		"areaDistrict": "天河区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Golang"],
		"welfareList": welfare or [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
	}


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	return instance


@patch("boss_agent_cli.commands.search.try_save_index")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_welfare_filter_tag_match(mock_auth, mock_client_cls, mock_cache_cls, mock_save):
	"""福利筛选：标签直接匹配不需要查详情"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [
				_make_job_raw("j1", welfare=["双休", "五险一金"], security_id="sec_1"),
			],
		},
	}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "search", "golang", "--welfare", "双休"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	assert parsed["data"][0]["job_id"] == "j1"
	# 标签已匹配，不需要查详情
	mock_client.job_card.assert_not_called()


@patch("boss_agent_cli.commands.search.try_save_index")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_welfare_filter_detail_fallback(mock_auth, mock_client_cls, mock_cache_cls, mock_save):
	"""福利筛选：标签不够时并行查详情"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [
				_make_job_raw("j1", welfare=[], security_id="sec_1"),
			],
		},
	}
	mock_client.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"postDescription": "本岗位提供双休、五险一金等福利",
			},
		},
	}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "search", "golang", "--welfare", "双休"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	# 确认调用了 job_card 查详情
	mock_client.job_card.assert_called_once()


@patch("boss_agent_cli.commands.search.try_save_index")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_welfare_filter_zhilian_data_envelope_detail_fallback(mock_auth, mock_client_cls, mock_cache_cls, mock_save):
	"""智联 data 包络应支持 search --welfare 的详情补抓。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"code": 200,
		"data": {
			"hasMore": False,
			"jobList": [
				_make_job_raw("j1", welfare=[], security_id="sec_1"),
			],
		},
	}
	mock_client.job_card.return_value = {
		"code": 200,
		"data": {
			"jobCard": {
				"postDescription": "本岗位提供周末双休和五险一金",
			},
		},
	}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "--platform", "zhilian", "search", "golang", "--welfare", "双休"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["job_id"] == "j1"
	assert "双休(描述)" in parsed["data"][0]["welfare_match"]
	mock_client.job_card.assert_called_once_with("sec_1", "")


def test_search_click_keeps_welfare_option():
	"""--welfare 必须持续暴露在 search CLI help 中。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["search", "--help"])
	assert result.exit_code == 0
	assert "--welfare" in result.output
	assert "福利筛选" in result.output
