import json

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_shortlist_add_list_remove(tmp_path):
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"shortlist", "add", "sec_001", "job_001",
			"--title", "Go 开发",
			"--company", "TestCo",
			"--city", "广州",
			"--salary", "20-30K",
			"--source", "search",
		],
	)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sec_001"

	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"])
	assert list_result.exit_code == 0
	list_parsed = json.loads(list_result.output)
	assert len(list_parsed["data"]) == 1
	assert list_parsed["data"][0]["company"] == "TestCo"

	remove_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "remove", "sec_001", "job_001"])
	assert remove_result.exit_code == 0
	remove_parsed = json.loads(remove_result.output)
	assert remove_parsed["data"]["removed"] is True


def test_shortlist_zhilian_hints_use_platform_specific_commands(tmp_path):
	runner = CliRunner()
	add_result = runner.invoke(
		cli,
		[
			"--data-dir", str(tmp_path),
			"--json",
			"--platform", "zhilian",
			"shortlist", "add", "sec_001", "job_001",
		],
	)
	assert add_result.exit_code == 0
	add_parsed = json.loads(add_result.output)
	assert add_parsed["hints"]["next_actions"][0] == "boss --platform zhilian shortlist list"
	assert add_parsed["hints"]["next_actions"][1] == "boss --platform zhilian shortlist remove sec_001 job_001"

	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "--platform", "zhilian", "shortlist", "list"])
	assert list_result.exit_code == 0
	list_parsed = json.loads(list_result.output)
	assert list_parsed["hints"]["next_actions"][0] == "boss --platform zhilian detail <security_id> --job-id <job_id>"

	remove_result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "--platform", "zhilian", "shortlist", "remove", "sec_001", "job_001"],
	)
	assert remove_result.exit_code == 0
	remove_parsed = json.loads(remove_result.output)
	assert remove_parsed["hints"]["next_actions"][0] == "boss --platform zhilian shortlist list"


def test_shortlist_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "shortlist" in parsed["data"]["commands"]
