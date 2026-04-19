import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
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


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_command_returns_structured_sections(mock_auth_cls, mock_client_cls):
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["follow_up_count"] >= 1
	assert parsed["data"]["interview_count"] == 1


def test_digest_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "digest" in parsed["data"]["commands"]


# ── --format md 支持 ────────────────────────────────────────


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_writes_markdown_to_stdout(mock_auth_cls, mock_client_cls):
	"""--format md 未指定 -o 时把 Markdown 写到 stdout，不套 JSON 信封"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["digest", "--format", "md"])
	assert result.exit_code == 0
	assert "# 每日求职摘要" in result.output
	assert "## 核心指标" in result.output
	assert "## 待跟进" in result.output
	assert "## 面试" in result.output
	# 不是 JSON 信封
	assert not result.output.lstrip().startswith("{")


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_with_output_path_writes_file(mock_auth_cls, mock_client_cls, tmp_path):
	"""--format md -o <path> 写文件并返回 JSON 信封说明路径"""
	_setup_digest_mocks(mock_client_cls)

	out = tmp_path / "digest.md"
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "digest", "--format", "md", "-o", str(out)])
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


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_contains_interview_details(mock_auth_cls, mock_client_cls):
	"""Markdown 中面试条目应含岗位名 / 公司 / 时间"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["digest", "--format", "md"])
	assert "Go 开发" in result.output
	assert "TestCo" in result.output
	assert "2026-04-14" in result.output


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_md_handles_empty_sections(mock_auth_cls, mock_client_cls):
	"""没有任何数据时 md 应写友好占位符而非抛异常"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"zpData": {"result": []}}
	mock_client.interview_data.return_value = {"zpData": {"interviewList": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["digest", "--format", "md"])
	assert result.exit_code == 0
	assert "# 每日求职摘要" in result.output
	# 空段落应有明确占位而非空白
	assert "暂无" in result.output or "无" in result.output


@patch("boss_agent_cli.commands.digest.BossClient")
@patch("boss_agent_cli.commands.digest.AuthManager")
def test_digest_format_json_default_unchanged(mock_auth_cls, mock_client_cls):
	"""未指定 format 时保持原来的 JSON 信封行为不变"""
	_setup_digest_mocks(mock_client_cls)

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "digest"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "new_match_count" in parsed["data"]
