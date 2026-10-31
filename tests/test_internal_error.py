"""兜底异常分类：确定性错误归为 INTERNAL_ERROR，其他异常仍是 NETWORK_ERROR。

TypeError / AttributeError / KeyError / IndexError 来自 CLI 缺陷或平台返回了未预期的
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


@pytest.mark.parametrize("exc", [TypeError("x"), AttributeError("x"), KeyError("x"), IndexError("x")])
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
