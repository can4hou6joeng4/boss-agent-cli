import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.display import risk_error_message
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _setup_digest_mocks(mock_client_cls):
	"""统一 mock 返回值，供多个测试复用。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {
		"zpData": {
			"result": [
				{
					"name": "张HR",
					"securityId": "sec_1",
					"uid": 99,
					"title": "HR",
					"brandName": "TestCo",
					"friendSource": 0,
					"encryptJobId": "job_1",
					"lastMsg": "请尽快回复",
					"lastTS": 1700000001000,
					"unreadMsgCount": 1,
					"relationType": 1,
					"lastMessageInfo": {"status": 1},
				},
			],
		},
	}
	mock_client.interview_data.return_value = {
		"zpData": {
			"interviewList": [
				{"jobName": "Go 开发", "brandName": "TestCo", "interviewTime": "2026-04-14 10:00", "statusDesc": "待面试"},
			],
		},
	}
	return mock_client


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_command_returns_structured_sections(mock_auth_cls, mock_client_cls, legacy_args):
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["follow_up_count"] >= 1
	assert parsed["data"]["interview_count"] == 1


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_aggregates_second_page_matches(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		{
			"zpData": {
				"result": [
					{
						"name": "张HR",
						"securityId": "sec_1",
						"uid": 99,
						"title": "HR",
						"brandName": "TestCo",
						"friendSource": 0,
						"encryptJobId": "job_1",
						"lastMsg": "请尽快回复",
						"lastTS": 1700000001000,
						"unreadMsgCount": 1,
						"relationType": 1,
						"lastMessageInfo": {"status": 1},
					},
				],
			},
		},
		{
			"zpData": {
				"result": [
					{
						"name": "李HR",
						"securityId": "sec_2",
						"uid": 100,
						"title": "HRBP",
						"brandName": "PageTwo Inc",
						"friendSource": 0,
						"encryptJobId": "job_2",
						"lastMsg": "第二页待回复",
						"lastTS": 1700000002000,
						"unreadMsgCount": 1,
						"relationType": 1,
						"lastMessageInfo": {"status": 1},
					},
				],
			},
		},
		{"zpData": {"result": []}},
	]
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	companies = {item["company"] for item in parsed["data"]["new_matches"]}
	assert "TestCo" in companies
	assert "PageTwo Inc" in companies
	assert parsed["data"]["new_match_count"] == 2


def test_digest_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "digest" in parsed["data"]["commands"]


def test_digest_rejects_overflowing_days_stale():
	"""`--days-stale` 是无界整数时会在时间运算里溢出，必须按参数错误提前拒绝。"""
	result = CliRunner().invoke(cli, ["digest", "--days-stale", "9" * 400])
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"


# ── --format md 支持 ────────────────────────────────────────


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_writes_markdown_to_stdout(mock_auth_cls, mock_client_cls, legacy_args):
	"""未指定 -o 且未强制 --json 时把 Markdown 写到 stdout，供管道重定向，不套 JSON 信封"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "digest", "--format", "md"])
	assert result.exit_code == 0
	assert "# 每日求职摘要" in result.output
	assert "## 核心指标" in result.output
	assert "## 待跟进" in result.output
	assert "## 面试" in result.output
	# 不是 JSON 信封
	assert not result.output.lstrip().startswith("{")


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_with_json_flag_returns_envelope(mock_auth_cls, mock_client_cls, legacy_args):
	"""--json 下 stdout 必须是信封：MCP 总是带 --json 调用，直出 Markdown 只会被判成 CLI_ERROR。"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest", "--format", "md"])
	assert result.exit_code == 0

	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "md"
	assert parsed["data"]["content"].startswith("# 每日求职摘要")
	assert "## 待跟进" in parsed["data"]["content"]
	assert parsed["data"]["bytes"] == len(parsed["data"]["content"].encode("utf-8"))


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_with_output_path_writes_file(mock_auth_cls, mock_client_cls, tmp_path, legacy_args):
	"""--format md -o <path> 写文件并返回 JSON 信封说明路径"""
	_setup_digest_mocks(mock_client_cls)

	out = tmp_path / "digest.md"
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest", "--format", "md", "-o", str(out)])
	assert result.exit_code == 0

	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "md"
	assert parsed["data"]["path"] == str(out)
	assert parsed["data"]["bytes"] > 0

	content = out.read_text(encoding="utf-8")
	assert content.startswith("# 每日求职摘要")
	assert "TestCo" in content
	assert "Go 开发" in content


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_output_path_expands_tilde_and_creates_parent(mock_auth_cls, mock_client_cls, tmp_path, monkeypatch, legacy_args):
	"""MCP 子进程调用不经 shell，`~` 不会被展开；-o 必须自己展开并建父目录。"""
	_setup_digest_mocks(mock_client_cls)
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))

	result = CliRunner().invoke(cli, [
		"--json", *legacy_args, "digest", "--format", "md", "-o", "~/nested/digest.md",
	])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	expected = tmp_path / "nested" / "digest.md"
	assert parsed["data"]["path"] == str(expected)
	assert expected.read_text(encoding="utf-8").startswith("# 每日求职摘要")


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_contains_interview_details(mock_auth_cls, mock_client_cls, legacy_args):
	"""Markdown 中面试条目应含岗位名 / 公司 / 时间"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "digest", "--format", "md"])
	assert "Go 开发" in result.output
	assert "TestCo" in result.output
	assert "2026-04-14" in result.output


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_handles_empty_sections(mock_auth_cls, mock_client_cls, legacy_args):
	"""没有任何数据时 md 应写友好占位符而非抛异常"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"zpData": {"result": []}}
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, [*legacy_args, "digest", "--format", "md"])
	assert result.exit_code == 0
	assert "# 每日求职摘要" in result.output
	# 空段落应有明确占位而非空白
	assert "暂无" in result.output or "无" in result.output


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_json_default_unchanged(mock_auth_cls, mock_client_cls, legacy_args):
	"""未指定 format 时保持原来的 JSON 信封行为不变"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "new_match_count" in parsed["data"]


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_reports_interview_error(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"zpData": {"result": []}}
	mock_client.interview_data.return_value = {"code": 36, "message": "account risk"}
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == risk_error_message("ACCOUNT_RISK")


@patch("boss_agent_cli.commands.digest.get_platform_instance")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_reports_second_page_friend_list_error(mock_auth_cls, mock_client_cls, legacy_args):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.side_effect = [
		{
			"zpData": {
				"result": [
					{
						"name": "张HR",
						"securityId": "sec_1",
						"uid": 99,
						"title": "HR",
						"brandName": "TestCo",
						"friendSource": 0,
						"encryptJobId": "job_1",
						"lastMsg": "请尽快回复",
						"lastTS": 1700000001000,
						"unreadMsgCount": 1,
						"relationType": 1,
						"lastMessageInfo": {"status": 1},
					},
				],
			},
		},
		{"code": 37, "message": "stoken expired"},
	]
	mock_client.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", *legacy_args, "digest"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "TOKEN_REFRESH_FAILED"
	assert parsed["error"]["message"] == "stoken expired"


def test_digest_renders_sections_as_tables(monkeypatch, legacy_args):
	"""TTY 下三个列表分别走表格，不再把整段 dict 列表塞进面板。

	``new_matches`` / ``follow_ups`` / ``interviews`` 是完整结果项；直接插值进
	面板会渲染成一大段 repr，几十条时既撑爆面板，也没法扫读公司、职位与未读。
	"""
	import io

	from rich.console import Console

	from boss_agent_cli import display

	with patch("boss_agent_cli.commands.digest.get_platform_instance") as mock_platform_cls, patch(
		"boss_agent_cli.commands.digest.AuthManager"
	):
		_setup_digest_mocks(mock_platform_cls)
		stream = io.StringIO()
		monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=100))
		monkeypatch.setattr(display, "is_json_mode", lambda ctx: False)
		result = CliRunner().invoke(cli, [*legacy_args, "digest"])

	text = stream.getvalue()
	assert result.exit_code == 0
	assert result.output.strip() == ""
	# 面板只放计数与摘要，列表内容走表格。
	assert "new_match_count: 1" in text
	assert "新匹配" in text
	assert "待跟进" in text
	assert "面试" in text
	assert "TestCo" in text
	assert "请尽快回复" in text
	# 表格里不再出现被折行的 dict repr。
	assert "'company':" not in text
