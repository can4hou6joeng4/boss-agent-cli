import json
import shlex
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli import display
from boss_agent_cli.display import risk_error_message
from boss_agent_cli.main import cli
from boss_agent_cli.search_filters import SearchPipelinePlatformError


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
	# TTY 渲染给了「boss watch list」的下一步，信封必须同样给出。
	assert remove_parsed["hints"]["next_actions"] == ["boss watch list"]

	missing_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "remove", "nope"])
	assert missing_result.exit_code == 0
	missing_parsed = json.loads(missing_result.output)
	assert missing_parsed["data"]["removed"] is False
	assert missing_parsed["hints"]["next_actions"] == ["boss watch list"]


def test_watch_remove_rejects_blank_name(tmp_path):
	"""空名字不可能命中任何 watch：不能以 removed=false 的 ok:true 报成功。"""
	runner = CliRunner()
	for name in ("", "  "):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "remove", name])
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "名称不能为空" in parsed["error"]["message"]
		# 空名字的恢复指引要能直接执行：空串既没有名字也查不到记录。
		assert parsed["hints"]["next_actions"] == ["boss watch list"]
		assert "boss watch list" in parsed["error"]["recovery_action"]


def test_watch_run_blank_name_points_at_list_and_all(tmp_path):
	"""``boss watch run ""`` 也要给出可选的两条路径：点名运行或 --all。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", ""])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["hints"]["next_actions"] == ["boss watch list", "boss watch run --all"]


def test_watch_list_empty_next_step_is_executable(tmp_path, monkeypatch):
	"""TTY 空列表的下一步提示必须是可执行形式：query 是位置参数。"""
	captured: dict[str, list[str]] = {}
	monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
	monkeypatch.setattr(display, "render_next_steps", lambda steps: captured.setdefault("steps", steps))

	runner = CliRunner()
	list_result = runner.invoke(cli, ["--data-dir", str(tmp_path), "watch", "list"])
	assert list_result.exit_code == 0, list_result.output
	steps = captured["steps"]
	assert steps == ["boss watch add <name> <关键词>"]

	hint_args = steps[0].replace("<name>", "hint-watch").replace("<关键词>", "golang").split(" ")[1:]
	added = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *hint_args])
	assert added.exit_code == 0, added.output


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_marks_only_new_items(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
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


def test_watch_run_renders_new_items_as_table(tmp_path, monkeypatch):
	"""TTY 下新增职位走表格，不再把整段 dict 列表塞进面板。

	``new_items`` 是完整搜索结果项；直接插值进面板会渲染成一大段 repr，几十条
	新职位时既撑爆面板，也没法扫读标题与薪资。
	"""

	def _run(*args):
		return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *args])

	with patch("boss_agent_cli.commands.watch.get_platform_instance") as mock_platform_cls, patch(
		"boss_agent_cli.commands.watch.AuthManager"
	):
		mock_client = _ctx_mock(mock_platform_cls)
		mock_client.is_success.return_value = True
		mock_client.search_jobs.return_value = {"zpData": {"hasMore": False, "jobList": [_job("sec-1", "job-1")]}}
		_run("--json", "watch", "add", "golang-gz", "golang", "--city", "广州")

		import io

		from rich.console import Console

		stream = io.StringIO()
		monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=100))
		monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
		result = _run("watch", "run", "golang-gz")

	text = stream.getvalue()
	assert result.exit_code == 0
	assert result.output.strip() == ""
	assert "new_count: 1" in text
	assert "新增职位" in text
	assert "Go 开发" in text
	# 表格里不再出现被折行的 dict repr。
	assert "'jobName':" not in text


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_dedupes_across_security_id_rotation(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
	mock_auth_cls.return_value.account_fingerprint.return_value = "acct_a"
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
	assert json.loads(first.output)["data"]["new_count"] == 1

	# 平台换发新的 securityId，但 encryptJobId 不变：同一账号下仍算已见。
	mock_client.search_jobs.return_value = {
		"zpData": {
			"hasMore": False,
			"jobList": [_job("sec-2", "job-1")],
		},
	}
	second = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert second.exit_code == 0
	second_parsed = json.loads(second.output)
	assert second_parsed["data"]["new_count"] == 0
	assert second_parsed["data"]["seen_count"] == 1


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_reports_platform_error(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
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
def test_watch_run_reports_detail_platform_error(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
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
def test_watch_run_reports_welfare_not_supported(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
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


def test_watch_add_rejects_blank_name_or_query(tmp_path):
	"""空名称 / 空关键词存不下可用的 watch：`watch run ` 缺参数，空关键词等于不筛选。"""
	runner = CliRunner()
	for name, query in (("blank", ""), ("", "golang"), ("  ", "golang")):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "watch", "add", name, query],
		)
		assert result.exit_code == 1, result.output
		parsed = json.loads(result.output)
		assert parsed["error"]["code"] == "INVALID_PARAM"

	listed = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"]).output)
	assert listed["data"] == []


def test_watch_schema_is_exposed():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "watch" in parsed["data"]["commands"]


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_risk_uses_stop_contract(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
	"""watch run 命中风控时信封必须是停止指引，而不是可恢复的普通失败。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "blocked")
	mock_client.search_jobs.return_value = {"code": 36, "message": "blocked"}

	runner = CliRunner()
	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "watch", "add", "golang-gz", "golang"],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")
	assert "blocked" not in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is False
	assert "停止自动化访问" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"]


@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_auth_expired_is_recoverable(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
	"""登录过期是可恢复错误，不能硬编码成不可恢复。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("AUTH_EXPIRED", "expired")
	mock_client.search_jobs.return_value = {"code": 401, "message": "expired"}

	runner = CliRunner()
	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "watch", "add", "golang-gz", "golang"],
	)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "golang-gz"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AUTH_EXPIRED"
	assert parsed["error"]["recoverable"] is True
	assert shlex.split(parsed["error"]["recovery_action"]) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "login",
	]


@patch("boss_agent_cli.commands.watch.run_search_pipeline")
@patch("boss_agent_cli.commands.watch.get_platform_instance")
@patch("boss_agent_cli.commands.watch.AuthManager")
def test_watch_run_all_stops_on_risk(mock_auth_cls, mock_client_cls, mock_pipeline, tmp_path, legacy_args):
	"""--all 里任一 watch 命中风控后必须停批，不能再向平台请求下一个 watch。"""
	_ctx_mock(mock_client_cls)
	mock_pipeline.side_effect = SearchPipelinePlatformError("ACCOUNT_RISK", "blocked")

	runner = CliRunner()
	for name in ("risk-first", "safe-second"):
		runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "watch", "add", name, "golang"],
		)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "--all"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")
	assert "blocked" not in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is False
	assert "停止自动化访问" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"]
	assert parsed["error"]["details"]["stopped_watch"] in {"risk-first", "safe-second"}
	assert mock_pipeline.call_count == 1


def _break_params(tmp_path, name, payload="{not json"):
	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", (payload, name))
	conn.commit()
	conn.close()


def test_watch_list_reports_corrupt_params(tmp_path):
	"""坏条目从列表里剔除，但要在 hints 里点名，避免用户以为 watch 丢了。"""
	runner = CliRunner()
	for name in ("good", "broken"):
		runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", name, "golang"])
	_break_params(tmp_path, "broken")

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	names = [item["name"] for item in parsed["data"]]
	assert "good" in names and "broken" not in names
	assert parsed["hints"]["corrupt"] == ["broken"]
	assert "boss watch remove" in parsed["hints"]["operator_actions"][0]


def test_watch_run_corrupt_params_reports_envelope(tmp_path):
	"""watch 参数写坏时给删除重建指引，不能兜成 NETWORK_ERROR「重试」。"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "broken", "golang"])
	_break_params(tmp_path, "broken")

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "broken"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert parsed["error"]["recoverable"] is True
	assert parsed["hints"]["next_actions"][0] == "boss watch remove broken"


@patch("boss_agent_cli.commands.watch._execute_single_watch")
def test_watch_run_all_skips_corrupt_params(mock_execute, tmp_path):
	"""--all 只跑参数可用的条目，坏记录写进 data.skipped 让调用方看得见。"""
	mock_execute.return_value = {
		"name": "good", "new_count": 0, "seen_count": 0, "total_count": 0, "new_items": [],
	}
	runner = CliRunner()
	for name in ("good", "broken"):
		runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", name, "golang"])
	_break_params(tmp_path, "broken")

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "--all"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	executed = [call.args[2] for call in mock_execute.call_args_list]
	assert "good" in executed and "broken" not in executed
	assert parsed["data"]["skipped"] == ["broken"]
	assert parsed["hints"]["corrupt"] == ["broken"]


def test_watch_run_params_type_drift_reports_saved_search_invalid(tmp_path):
	"""参数是合法 JSON 但值类型漂移时不能兜成 INTERNAL_ERROR。

	`{"query": ["golang"]}` 能通过 `json.loads`，旧实现到 `_parse_watch_filters`
	的 `.split()` 才抛 AttributeError，报成 `INTERNAL_ERROR`「不要重试」，而实际
	修复方式是删掉重建这条本地记录。
	"""
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "drifted", "golang"])
	_break_params(tmp_path, "drifted", '{"query": ["golang"], "city": 123}')

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "drifted"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_INVALID"
	assert parsed["error"]["recoverable"] is True
	assert "query" in parsed["error"]["message"]
	assert "list" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"][0] == "boss watch remove drifted"


def test_watch_run_missing_reports_saved_search_not_found(tmp_path):
	"""watch 名不存在是本地记录缺失：给出已有条目与重建命令。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "run", "ghost"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "SAVED_SEARCH_NOT_FOUND"
	assert parsed["error"]["recovery_action"] == "boss preset list（预设）或 boss watch list（watch）"
	assert parsed["hints"]["next_actions"] == ["boss watch list", "boss watch add ghost <关键词>"]


def test_watch_list_json_hints_follow_the_actual_list(tmp_path):
	"""JSON 的 next_actions 也要按列表状态切换：空列表没有可运行 / 可删除的名字。"""
	runner = CliRunner()

	empty = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"]).output)
	assert empty["data"] == []
	assert empty["hints"]["next_actions"] == ["boss watch add <name> <关键词>"]

	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "add", "golang", "golang"])
	filled = json.loads(runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "watch", "list"]).output)
	assert [item["name"] for item in filled["data"]] == ["golang"]
	assert filled["hints"]["next_actions"] == ["boss watch run <name>", "boss watch remove <name>"]
