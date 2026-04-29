import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _job(security_id: str, job_id: str, title: str = "Go 开发"):
	return {
		"encryptJobId": job_id,
		"jobName": title,
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
		"securityId": security_id,
	}


def test_watch_add_list_remove(tmp_path):
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"watch", "add", "golang-gz", "golang",
			"--city", "广州",
			"--welfare", "双休",
		],
	)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["name"] == "golang-gz"

	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"])
	assert list_result.exit_code == 0
	list_parsed = json.loads(list_result.output)
	assert list_parsed["data"][0]["name"] == "golang-gz"

	remove_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "remove", "golang-gz"])
	assert remove_result.exit_code == 0
	remove_parsed = json.loads(remove_result.output)
	assert remove_parsed["data"]["removed"] is True


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_marks_only_new_items(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.return_value = True
	mock_client.search_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [_job("sec-1", "job-1")],
		},
	}

	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"watch", "add", "golang-gz", "golang",
			"--city", "广州",
		],
	)

	first = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert first.exit_code == 0
	first_parsed = json.loads(first.output)
	assert first_parsed["data"]["new_count"] == 1

	second = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert second.exit_code == 0
	second_parsed = json.loads(second.output)
	assert second_parsed["data"]["new_count"] == 0


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_reports_platform_error(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("UPSTREAM_ERROR", "service unavailable")
	mock_client.search_jobs.return_value = {"code": 500, "message": "service unavailable"}

	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"watch", "add", "golang-gz", "golang",
		],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "UPSTREAM_ERROR"


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_reports_detail_platform_error(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	mock_client.parse_error.side_effect = lambda response: (response.get("error_code", "UNKNOWN"), response.get("message", ""))
	mock_client.search_jobs.return_value = {
		"code": 0,
		"zpData": {
			"hasMore": False,
			"jobList": [_job("sec-1", "job-1", title="Go 开发") | {"welfareList": []}],
		},
	}
	mock_client.job_card.return_value = {"code": 500, "message": "detail unavailable", "error_code": "DETAIL_ERROR"}

	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"watch", "add", "golang-gz", "golang",
			"--welfare", "双休",
		],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "DETAIL_ERROR"


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_reports_welfare_not_supported(mock_auth_cls, mock_client_cls, tmp_path):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	mock_client.search_jobs.return_value = {
		"code": 0,
		"zpData": {
			"hasMore": False,
			"jobList": [_job("sec-1", "job-1", title="Go 开发") | {"welfareList": []}],
		},
	}
	mock_client.job_card.side_effect = NotImplementedError("unsupported")

	runner = CliRunner()
	runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"watch", "add", "golang-gz", "golang",
			"--welfare", "双休",
		],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "NOT_SUPPORTED"


def test_watch_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "watch" in parsed["data"]["commands"]
