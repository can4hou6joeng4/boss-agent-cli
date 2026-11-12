import json
import shlex
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _hinted(tmp_path, *parts):
	"""提示命令的期望文本：按当前数据目录重建（Issue #919）。"""
	return " ".join(["boss --data-dir", str(tmp_path.resolve()), *parts])


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
	# TTY 渲染给了「boss preset list」的下一步，信封必须同样给出。
	assert remove_parsed["hints"]["next_actions"] == [_hinted(tmp_path, "preset list")]

	missing_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "remove", "nope"])
	assert missing_result.exit_code == 0
	missing_parsed = json.loads(missing_result.output)
	assert missing_parsed["data"]["removed"] is False
	assert missing_parsed["hints"]["next_actions"] == [_hinted(tmp_path, "preset list")]


def test_preset_remove_rejects_blank_name(tmp_path):
	"""空名字不可能命中任何预设：不能以 removed=false 的 ok:true 报成功。"""
	runner = CliRunner()
	for name in ("", "  "):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "remove", name])
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "名称不能为空" in parsed["error"]["message"]
		# 空名字的恢复指引要能直接执行：空串既没有名字也查不到记录。
		assert parsed["hints"]["next_actions"] == [_hinted(tmp_path, "preset list")]
		assert _hinted(tmp_path, "preset list") in parsed["error"]["recovery_action"]


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
	assert steps == [_hinted(tmp_path, "preset add <name> <关键词>")]

	# 提示自带数据目录，去掉开头的 boss 即可执行。
	hint_args = [part.replace("<name>", "hint-preset").replace("<关键词>", "golang")
		for part in shlex.split(steps[0])[1:]]
	added = runner.invoke(cli, ["--json", *hint_args])
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
	assert _hinted(tmp_path, "preset remove legacy") in parsed["error"]["message"]


def test_search_with_invalid_preset_quotes_name_in_message(tmp_path):
	"""消息里的删除命令同样要保住带空格的预设名。

	`boss preset remove 我的 列表` 拆成两个参数后命中错误的名字，另外两条
	hints.next_actions 已走 shell_argument()，这条正文是同一类残留。
	"""
	runner = CliRunner()
	runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "我的 列表", "golang"]
	)
	_break_params(tmp_path, "我的 列表", json.dumps({"query": "golang", "salary": "天价"}))

	result = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "我的 列表"]
	)

	assert result.exit_code == 1, result.output
	message = json.loads(result.output)["error"]["message"]
	assert _hinted(tmp_path, "preset remove '我的 列表'") in message
	assert _hinted(tmp_path, "preset remove 我的 列表") not in message


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
	assert _hinted(tmp_path, "preset remove") in parsed["hints"]["operator_actions"][0]


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
	assert parsed["hints"]["next_actions"][0] == _hinted(tmp_path, "preset remove broken")


def test_search_with_missing_preset_reports_saved_search_not_found(tmp_path):
	"""预设在本地缓存里不存在，不是平台职位下架。

	以前报 JOB_NOT_FOUND（登记表文案「职位不存在或已下架」、recovery_action 为
	null）：Agent 会以为职位被撤下而跳过，既看不出是预设名写错，也没有下一步。
	"""
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "ghost"]
	)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_NOT_FOUND"
	assert parsed["error"]["recovery_action"] == f"用 {_hinted(tmp_path, 'preset list')} 查看已保存的预设后重试"
	assert parsed["hints"]["next_actions"] == [
		_hinted(tmp_path, "preset list"),
		_hinted(tmp_path, "preset add ghost <关键词>"),
	]


def test_search_with_missing_preset_quotes_name_with_spaces(tmp_path):
	"""预设名带空格时，重建它的 hint 必须整体加引号，否则 `add 我的 列表 <关键词>`
	会被拆成「我的」和「列表」两个位置参数。"""
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "我的 列表"]
	)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_NOT_FOUND"
	add_hint = parsed["hints"]["next_actions"][1]
	assert add_hint == _hinted(tmp_path, "preset add '我的 列表' <关键词>")
	assert shlex.split(add_hint) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "preset", "add", "我的 列表", "<关键词>",
	]


def test_search_with_corrupt_preset_quotes_name_with_spaces(tmp_path):
	"""删除重建指引同样要保住带空格的预设名。"""
	runner = CliRunner()
	runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "我的 列表", "python"]
	)
	_break_params(tmp_path, "我的 列表")

	result = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "我的 列表"]
	)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert parsed["hints"]["next_actions"] == [
		_hinted(tmp_path, "preset remove '我的 列表'"),
		_hinted(tmp_path, "preset add '我的 列表' <关键词>"),
	]


def test_preset_list_json_hints_follow_the_actual_list(tmp_path):
	"""JSON 的 next_actions 也要按列表状态切换：空列表没有可搜索 / 可删除的名字。

	TTY 提示早已按空列表给出 `preset add`，但 `hints.next_actions` 仍是
	`boss search --preset <name>` / `boss preset remove <name>`——Agent 照抄
	只会把一份从没存在过的预设名传给命令，再换回 SAVED_SEARCH_NOT_FOUND。
	"""
	runner = CliRunner()

	empty = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"]).output)
	assert empty["data"] == []
	assert empty["hints"]["next_actions"] == [_hinted(tmp_path, "preset add <name> <关键词>")]

	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "golang", "golang"])
	filled = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "list"]).output)
	assert [item["name"] for item in filled["data"]] == ["golang"]
	assert filled["hints"]["next_actions"] == [
		_hinted(tmp_path, "search --preset <name>"),
		_hinted(tmp_path, "preset remove <name>"),
	]


def test_preset_hint_quotes_name_with_spaces(tmp_path):
	"""预设名是自由文本：带空格时 hint 必须整体加引号，Agent 照抄才不会拆成两个参数。"""
	runner = CliRunner()
	added = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "我的 列表", "golang"]
	)
	assert added.exit_code == 0, added.output
	parsed = json.loads(added.output)
	assert _hinted(tmp_path, "search --preset '我的 列表'") in parsed["hints"]["next_actions"]

	hint = next(a for a in parsed["hints"]["next_actions"] if " search " in a)
	assert shlex.split(hint) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "search", "--preset", "我的 列表",
	]


@patch("boss_agent_cli.index_cache.save_index")
@patch("boss_agent_cli.commands.search.account_scope", return_value="acct")
@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.get_platform_instance")
@patch("boss_agent_cli.commands.search.AuthManager")
def test_preset_add_hint_finds_preset_in_same_data_dir(
	mock_auth_cls, mock_client_cls, mock_pipeline, mock_scope, mock_save_index, tmp_path,
):
	"""``preset add`` 的下一步提示要在同一数据目录里找得到刚保存的预设（Issue #919）。

	提示写成裸的 ``boss search --preset <名称>`` 时，照抄会落到默认目录，
	刚保存的预设立刻 ``SAVED_SEARCH_NOT_FOUND``。
	"""
	_ctx_mock(mock_client_cls)
	mock_pipeline.return_value = SimpleNamespace(
		items=[],
		has_more=False,
		has_more_reported=False,
		total=0,
		stats=SimpleNamespace(pages_scanned=1, jobs_seen=0, jobs_prefiltered=0, detail_checks=0),
	)

	runner = CliRunner()
	added = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "preset", "add", "golang-gz", "golang", "--city", "广州"],
	)
	assert added.exit_code == 0, added.output
	hint = json.loads(added.output)["hints"]["next_actions"][0]
	assert hint == _hinted(tmp_path, "search --preset golang-gz")

	# 提示自带数据目录，去掉开头的 boss 即可直接执行。
	result = runner.invoke(cli, ["--json", *shlex.split(hint)[1:]])
	assert result.exit_code == 0, result.output
	criteria = mock_pipeline.call_args.kwargs["criteria"]
	assert criteria.query == "golang"
	assert criteria.city == "广州"
