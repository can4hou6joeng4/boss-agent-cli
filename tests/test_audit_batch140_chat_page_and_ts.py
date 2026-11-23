"""审计批次 140：chat 分页页码上限与时间戳展示边界（Issue #2542）。

- ``chat --page`` 此前无上限：4300 位页码在 ``page + 1`` 字符串化时抛
  ``ValueError``，被兜成可重试的 ``NETWORK_ERROR``；信封分页也没有钳制。
- ``_format_ts`` 只保护了 ``fromtimestamp``，本地时区偏移把 UTC 边界时间推过
  year 10000 时 ``astimezone()`` 抛 ``OverflowError``，整条 chat / 导出失败；
  未来时间戳显示负的「N天前」。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.commands.chat import _format_ts
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE

_MAX_TS = 253402300799999  # UTC 9999-12-31 23:59:59.999


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friend(ts_ms: int = 1700000000000) -> dict:
	return {
		"uid": 10001,
		"securityId": "sid_current",
		"name": "联系人甲",
		"brandName": "示例公司",
		"title": "招聘专员",
		"lastTS": ts_ms,
	}


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_rejects_oversized_page_before_request(mock_auth_cls, mock_client_cls, tmp_path: Path) -> None:
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	platform = _ctx_mock(mock_client_cls)
	platform.friend_list.return_value = {"zpData": {"result": [_friend()], "hasMore": True}}

	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "chat", "--page", "9" * 4300]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	platform.friend_list.assert_not_called()


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_at_trusted_page_limit_does_not_offer_next_page(
	mock_auth_cls, mock_client_cls, tmp_path: Path
) -> None:
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	platform = _ctx_mock(mock_client_cls)
	platform.friend_list.return_value = {"zpData": {"result": [_friend()], "hasMore": True}}

	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "chat", "--page", str(MAX_TRUSTED_PAGE)]
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert payload["pagination"]["has_more"] is True
	assert not [action for action in payload["hints"]["next_actions"] if "chat --page" in action]
	platform.friend_list.assert_called_once_with(page=MAX_TRUSTED_PAGE)


def test_chat_schema_page_has_maximum() -> None:
	page = SCHEMA_DATA["commands"]["chat"]["options"]["--page"]

	assert page["minimum"] == 1
	assert page["maximum"] == MAX_TRUSTED_PAGE
	# MCP 工具目录是手写的第二来源，两处必须一致（tests/test_tool_schema_bounds.py）。
	catalog = {tool.name: tool.input_schema for tool in build_tool_catalog()}
	assert catalog["boss_chat"]["properties"]["page"]["maximum"] == MAX_TRUSTED_PAGE


def test_format_ts_boundary_timestamp_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
	original = os.environ.get("TZ")

	monkeypatch.setenv("TZ", "Asia/Shanghai")
	time.tzset()
	try:
		# UTC 9999-12-31 23:59 + 8 小时越过 year 10000 上限；只影响展示。
		assert _format_ts(_MAX_TS) == "-"
	finally:
		if original is None:
			monkeypatch.delenv("TZ", raising=False)
		else:
			monkeypatch.setenv("TZ", original)
		time.tzset()


def test_format_ts_future_timestamp_is_absolute_not_negative_days() -> None:
	future_ms = int(
		(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).timestamp() * 1000
	)

	text = _format_ts(future_ms)

	assert text != "-"
	assert "天前" not in text
	assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text)


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_chat_export_survives_boundary_timestamp(
	mock_auth_cls, mock_client_cls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {}}
	platform = _ctx_mock(mock_client_cls)
	platform.friend_list.return_value = {"zpData": {"result": [_friend(_MAX_TS)], "hasMore": False}}
	target = tmp_path / "chat.json"
	original = os.environ.get("TZ")
	monkeypatch.setenv("TZ", "Asia/Shanghai")
	time.tzset()
	try:
		result = CliRunner().invoke(
			cli,
			[
				"--json",
				"--data-dir", str(tmp_path),
				"chat", "--export", "json", "-o", str(target),
			],
		)
	finally:
		if original is None:
			monkeypatch.delenv("TZ", raising=False)
		else:
			monkeypatch.setenv("TZ", original)
		time.tzset()

	assert result.exit_code == 0, result.output
	assert target.exists()
