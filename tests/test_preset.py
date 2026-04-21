import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _job():
	return {
		"encryptJobId": "job_001",
		"jobName": "Go 开发",
		"brandName": "TestCo",
		"salaryDesc": "20-30K",
		"cityName": "广州",
		"areaDistrict": "天河区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Go"],
		"welfareList": ["双休"],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": "sec_001",
	}


def test_preset_add_list_remove(tmp_path):
	runner = CliRunner()
	add_result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"preset", "add", "golang-gz", "golang",
			"--city", "广州",
			"--salary", "20-50K",
		],
	)
	assert add_result.exit_code == 0
	add_parsed = json.loads(add_result.output)
	assert add_parsed["data"]["name"] == "golang-gz"

	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"])
	assert list_result.exit_code == 0
	list_parsed = json.loads(list_result.output)
	assert list_parsed["data"][0]["name"] == "golang-gz"

	remove_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "remove", "golang-gz"])
	assert remove_result.exit_code == 0
	remove_parsed = json.loads(remove_result.output)
	assert remove_parsed["data"]["removed"] is True


@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.try_save_index")
def test_search_can_use_preset(mock_save, mock_cache_cls, mock_auth_cls, mock_client_cls, tmp_path):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.get_search.return_value = None
	mock_cache.get_saved_search.return_value = {
		"name": "golang-gz",
		"params": {"query": "golang", "city": "广州", "salary": "20-50K"},
	}
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {"zpData": {"hasMore": False, "jobList": [_job()]}}

	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"preset", "add", "golang-gz", "golang",
			"--city", "广州",
			"--salary", "20-50K",
		],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "golang-gz"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["title"] == "Go 开发"
	mock_client.search_jobs.assert_called_once()
	assert mock_client.search_jobs.call_args.kwargs["city"] == "广州"
	assert mock_client.search_jobs.call_args.kwargs["salary"] == "20-50K"


def test_preset_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "preset" in parsed["data"]["commands"]
