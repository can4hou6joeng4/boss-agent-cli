import json
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _break_params(tmp_path, name, payload="{not json"):
	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", (payload, name))
	conn.commit()
	conn.close()


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


def test_preset_list_empty_next_step_is_executable(tmp_path, monkeypatch):
	"""TTY 空列表的下一步提示必须是可执行形式：query 是位置参数。

	提示里写成 `--query` 时，照抄只会换回「No such option '--query'」。
	"""
	captured: dict[str, list[str]] = {}
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "render_next_steps", lambda steps: captured.setdefault("steps", steps))

	runner = CliRunner()
	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "preset", "list"])
	assert list_result.exit_code == 0, list_result.output
	steps = captured["steps"]
	assert steps == ["boss preset add <name> <关键词>"]

	hint_args = steps[0].replace("<name>", "hint-preset").replace("<关键词>", "golang").split(" ")[1:]
	added = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *hint_args])
	assert added.exit_code == 0, added.output


def test_preset_add_rejects_unknown_filters(tmp_path):
	"""未知薪资 / 经验 / 学历在保存时就拒绝，不能存下一条之后每次 search 都失败的预设。"""
	runner = CliRunner()
	for option, value in (("--salary", "天价"), ("--experience", "十年"), ("--education", "幼儿园")):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "preset", "add", "bad", "golang", option, value],
		)
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"

	listed = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"]).output)
	assert listed["data"] == []


def test_preset_add_rejects_blank_name_or_query(tmp_path):
	"""空名称 / 空关键词存不下可用的预设：既没法点名，也等于不筛选。"""
	runner = CliRunner()
	for name, query in (("blank", ""), ("", "golang"), ("  ", "golang")):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "preset", "add", name, query],
		)
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"

	listed = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"]).output)
	assert listed["data"] == []


def test_search_with_invalid_preset_names_the_preset(tmp_path):
	"""旧版本存下的非法预设：filter 校验失败时要点名是哪条预设，用户才知道删哪条。"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "legacy", "golang"])
	_break_params(tmp_path, "legacy", json.dumps({"query": "golang", "salary": "天价"}))

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "legacy"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "来自 preset 'legacy'" in parsed["error"]["message"]
	assert "boss preset remove legacy" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.try_save_index")
def test_search_can_use_preset(mock_save, mock_cache_cls, mock_auth_cls, mock_client_cls, tmp_path):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.get_search.return_value = None
	mock_cache.reserve_crawl_budget.return_value = 0.0
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


def test_preset_list_reports_corrupt_params(tmp_path):
	"""一条坏数据不能拖垮整张列表，也不能让坏条目假装不存在。"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "good", "golang"])
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "broken", "python"])
	_break_params(tmp_path, "broken")

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	names = [item["name"] for item in parsed["data"]]
	assert "good" in names and "broken" not in names
	assert parsed["hints"]["corrupt"] == ["broken"]
	assert "boss preset remove" in parsed["hints"]["operator_actions"][0]


def test_search_with_type_drifted_preset_reports_envelope(tmp_path):
	"""合法 JSON 但值类型漂移同样按预设损坏处理，而不是把 AttributeError 兜成内部错误。"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "drifted", "python"])
	_break_params(tmp_path, "drifted", '{"query": {"a": 1}}')

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "drifted"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert parsed["error"]["recoverable"] is True
	assert "query" in parsed["error"]["message"]


def test_search_with_corrupt_preset_reports_envelope(tmp_path):
	"""预设参数写坏时给可执行的删除重建指引，而不是 NETWORK_ERROR「重试」。"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "broken", "python"])
	_break_params(tmp_path, "broken")

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "broken"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"]
	assert "broken" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"][0] == "boss preset remove broken"
