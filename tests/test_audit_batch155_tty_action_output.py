"""登录 / 退出 / 候选池写操作 / 收藏同步 / digest 的终端结果（Issue #2769）。"""

import io
import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 110) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), *args])


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def test_login_success_renders_panel_and_next_steps(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth_cls:
		mock_auth_cls.return_value.login.return_value = {"_method": "扫码"}
		result = _invoke(tmp_path, "login")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "登录成功（扫码）" in out
	assert "下一步" in out
	for command in ("status", "search <query>", "recommend"):
		assert command in out
	# JSON 信封契约不变：同一命令在 --json 下仍只有信封。
	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth_cls:
		mock_auth_cls.return_value.login.return_value = {"_method": "扫码"}
		json_result = _invoke(tmp_path, "--json", "login")
	assert json.loads(json_result.stdout)["data"]["message"] == "登录成功（扫码）"


def test_login_failure_renders_error_and_operator_actions(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth_cls:
		mock_auth_cls.return_value.login.side_effect = ConnectionError("connection refused")
		result = _invoke(tmp_path, "login")
	assert result.exit_code == 1
	assert not result.stdout
	out = stream.getvalue()
	assert "CDP_UNAVAILABLE" in out
	assert "recovery:" in out
	# 扫码 / 检查网络等真人动作必须在终端可见（此前只藏在 JSON 信封里）。
	assert "--remote-debugging-port=9222" in out
	with patch("boss_agent_cli.commands.login.AuthManager") as mock_auth_cls:
		mock_auth_cls.return_value.login.side_effect = ConnectionError("connection refused")
		json_result = _invoke(tmp_path, "--json", "login")
	parsed = json.loads(json_result.stdout)
	assert parsed["error"]["code"] == "CDP_UNAVAILABLE"
	assert "--remote-debugging-port=9222" in parsed["hints"]["operator_actions"][0]


def test_logout_renders_result_and_recovery(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	with patch("boss_agent_cli.commands.logout.AuthManager") as mock_auth_cls:
		result = _invoke(tmp_path, "logout")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	assert "已退出登录" in stream.getvalue()
	assert "login" in stream.getvalue()

	stream.seek(0)
	stream.truncate()
	with patch("boss_agent_cli.commands.logout.AuthManager") as mock_auth_cls:
		mock_auth_cls.return_value.logout.side_effect = OSError("disk full")
		failed = _invoke(tmp_path, "logout")
	assert failed.exit_code == 1
	assert not failed.stdout
	out = stream.getvalue()
	assert "LOGOUT_FAILED" in out
	assert "disk full" in out
	assert "recovery:" in out


def test_shortlist_write_actions_render_in_tty(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	added = _invoke(
		tmp_path,
		"shortlist", "add", "sid-demo", "job-demo",
		"--title", "后端工程师", "--company", "示例公司", "--salary", "25-40K",
	)
	assert added.exit_code == 0, added.output or repr(added.exception)
	assert not added.stdout
	out = stream.getvalue()
	assert "shortlist add" in out
	assert "后端工程师" in out
	assert "25-40K" in out
	assert "shortlist list" in out

	stream.seek(0)
	stream.truncate()
	annotated = _invoke(tmp_path, "shortlist", "annotate", "sid-demo", "job-demo", "--add-tag", "远程")
	assert annotated.exit_code == 0, annotated.output or repr(annotated.exception)
	assert not annotated.stdout
	out = stream.getvalue()
	assert "shortlist annotate" in out
	assert "远程" in out

	stream.seek(0)
	stream.truncate()
	removed = _invoke(tmp_path, "shortlist", "remove", "sid-demo", "job-demo")
	assert removed.exit_code == 0, removed.output or repr(removed.exception)
	assert not removed.stdout
	out = stream.getvalue()
	assert "shortlist remove" in out
	assert "removed: True" in out

	# 三种写操作的 JSON 契约不变。
	json_added = json.loads(
		_invoke(
			tmp_path,
			"--json",
			"shortlist", "add", "sid-demo", "job-demo",
			"--title", "后端工程师", "--company", "示例公司", "--salary", "25-40K",
		).stdout
	)
	assert json_added["data"]["action"] == "add"
	assert json_added["data"]["title"] == "后端工程师"


def test_digest_markdown_output_renders_result(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	# 面板按终端宽度折行：用足够宽的终端让整条路径完整显示。
	stream = _tty(monkeypatch, width=220)
	target = tmp_path / "digest.md"
	with patch("boss_agent_cli.commands.digest.get_platform_instance") as mock_platform_cls, \
		patch("boss_agent_cli.commands.digest.AuthManager"):
		platform = _ctx_mock(mock_platform_cls)
		platform.friend_list.return_value = {"zpData": {"result": []}}
		platform.interview_data.return_value = {"zpData": {"interviewList": []}}
		result = _invoke(tmp_path, "digest", "--format", "md", "-o", str(target))
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	assert target.is_file()
	out = stream.getvalue()
	assert "digest" in out
	assert str(target) in out
	assert "bytes" in out
	assert "邮件客户端" in out


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_renders_counts(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {
			"cardList": [
				{
					"securityId": "sec_demo",
					"encryptJobId": "job_demo",
					"jobName": "示例职位",
					"brandName": "示例公司",
					"cityName": "北京",
					"jobSalary": "25-40K",
					"jobLabels": ["双休"],
					"jobValidStatus": 1,
				},
			],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "favorites", "sync")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "favorites sync" in out
	assert "imported_count: 1" in out
	assert "active_count: 1" in out
	# 后继命令仍带数据目录，与 JSON 信封同源。
	list_cmd = json.loads(_invoke(tmp_path, "--json", "favorites", "sync").stdout)["hints"]["next_actions"][0]
	assert shlex.split(list_cmd)[:3] == ["boss", "--data-dir", str(tmp_path.resolve())]
