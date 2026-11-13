"""兜底异常分类：确定性错误归为 INTERNAL_ERROR，其他异常仍是 NETWORK_ERROR。

TypeError / AttributeError / KeyError / IndexError / OverflowError 来自 CLI 缺陷或平台返回了未预期的
数据结构，同样的输入必然得到同样的结果。以前一律报成可重试的 NETWORK_ERROR，Agent
会反复重试，重试的若是平台请求还会白白消耗请求预算。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli import output
from boss_agent_cli.main import cli
from boss_agent_cli.schema.error_codes import ERROR_CODES


def _ctx(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _recommend_raising(exc, *global_args):
	with patch("boss_agent_cli.commands.recommend.get_platform_instance") as platform_cls, \
		patch("boss_agent_cli.commands.recommend.AuthManager"), \
		patch("boss_agent_cli.commands.recommend.CacheStore") as cache_cls:
		_ctx(cache_cls)
		_ctx(platform_cls).recommend_jobs.side_effect = exc
		return CliRunner().invoke(cli, [*global_args, "--json", "recommend"])


def test_internal_error_is_registered_as_not_recoverable():
	spec = ERROR_CODES["INTERNAL_ERROR"]
	assert spec["recoverable"] is False
	assert "不要重试" in spec["recovery_action"]


@pytest.mark.parametrize(
	"exc", [TypeError("x"), AttributeError("x"), KeyError("x"), IndexError("x"), OverflowError("x")]
)
def test_deterministic_errors_become_internal_error(exc):
	result = _recommend_raising(exc)
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert payload["error"]["message"].startswith(f"recommend 失败: {type(exc).__name__}: ")
	assert result.exit_code == 1


@pytest.mark.parametrize("exc", [OSError("reset"), RuntimeError("browser closed"), ValueError("bad json")])
def test_other_errors_keep_network_error(exc):
	payload = json.loads(_recommend_raising(exc).output)
	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True


@pytest.mark.parametrize(
	"exc",
	[
		PermissionError(13, "Permission denied", "/tmp/boss-agent/config.json"),
		IsADirectoryError(21, "Is a directory", "/tmp/boss-agent/config.json"),
		FileNotFoundError(2, "No such file or directory", "/tmp/boss-agent/export.json"),
		OSError(28, "No space left on device", "/tmp/boss-agent/export.json"),
	],
)
def test_local_filesystem_errors_become_local_io_error(exc):
	"""文件不可写不是网络问题：报成可重试的 NETWORK_ERROR 会让 Agent 无限重试同一命令。"""
	payload = json.loads(_recommend_raising(exc).output)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
	assert "不要自动重试" in payload["error"]["recovery_action"]
	assert "本地文件读写失败" in payload["error"]["message"]


@pytest.mark.parametrize("exc", [ConnectionResetError(104, "Connection reset by peer"), TimeoutError("timed out")])
def test_socket_errors_stay_network_error(exc):
	"""ConnectionError / TimeoutError 是 OSError 子类，但仍是网络问题，保持可重试语义。"""
	payload = json.loads(_recommend_raising(exc).output)
	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True


def test_uncaught_oserror_reports_actual_command_name(tmp_path):
	"""顶层 IO 兜底以前把所有写文件失败都记成 command=boss：看不出是哪个子命令，
	按 command 过滤日志或按信封分发的调用方也会认错命令。

	父目录不存在已不再是失败条件（`-o` 会自动建父目录），这里用同名普通文件
	占住父路径触发 mkdir 的 OSError。
	"""
	blocker = tmp_path / "missing"
	blocker.write_text("not a directory", encoding="utf-8")
	result = CliRunner().invoke(cli, [
		"--data-dir", str(tmp_path),
		"stats", "--format", "html", "-o", str(blocker / "report.html"),
	])
	payload = json.loads(result.output)
	assert result.exit_code == 1
	assert payload["command"] == "stats"
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["message"].startswith("stats 本地文件读写失败: ")
	assert payload["error"]["recoverable"] is False
	# recovery_action 说的「确认目标路径可写 / 磁盘空间正常」是给真人的自然语言；
	# hints.operator_actions 才是信封里可读的下一步，此前这里是 null。
	assert payload["hints"]["operator_actions"]


def test_group_network_fallback_does_not_add_local_io_hint(tmp_path):
	"""网络类异常不该带「确认目标路径可写」这条与原因无关的指引。"""
	with patch(
		"boss_agent_cli.main._resolve_data_dir",
		side_effect=ConnectionResetError("connection reset by peer"),
	):
		result = CliRunner().invoke(cli, ["--json", "stats"])

	payload = json.loads(result.output)
	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["hints"] is None


def test_local_io_error_is_registered_as_not_recoverable():
	spec = ERROR_CODES["LOCAL_IO_ERROR"]
	assert spec["recoverable"] is False
	assert "不要自动重试" in spec["recovery_action"]


def test_internal_error_exits_70_in_sysexits_mode():
	try:
		result = _recommend_raising(TypeError("x"), "--exit-codes", "sysexits")
	finally:
		output.set_exit_codes_mode("envelope")
	assert result.exit_code == 70
	assert json.loads(result.output)["error"]["code"] == "INTERNAL_ERROR"


def test_me_reports_internal_error():
	with patch("boss_agent_cli.commands.me.get_platform_instance") as platform_cls, \
		patch("boss_agent_cli.commands.me.AuthManager"):
		_ctx(platform_cls).user_info.side_effect = AttributeError("'NoneType' object has no attribute 'get'")
		result = CliRunner().invoke(cli, ["--json", "me"])
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["message"].startswith("AttributeError: ")
	assert payload["error"]["recoverable"] is False


def test_overflow_in_undecorated_command_becomes_internal_error(tmp_path):
	"""没有 ``handle_auth_errors`` 包装的本地命令（stats）也要给信封。

	``stats --days 10**400`` 会让 ``time.time() - days * 86400`` 抛 OverflowError；
	这条路径既没有装饰器、也不在 main.py 的 OSError 网兜里，以前 stdout 只有
	traceback，违反「每条命令只输出一个信封」的契约。
	"""
	with patch(
		"boss_agent_cli.commands.stats._collect_stats",
		side_effect=OverflowError("int too large to convert to float"),
	):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "stats"])
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["command"] == "stats"
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert payload["error"]["message"].startswith("stats 失败: OverflowError: ")
