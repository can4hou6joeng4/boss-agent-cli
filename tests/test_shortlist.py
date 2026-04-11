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


def test_shortlist_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "shortlist" in parsed["data"]["commands"]
