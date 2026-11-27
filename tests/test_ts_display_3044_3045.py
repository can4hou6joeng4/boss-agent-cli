"""终端时间列的可信下界与 activeTime 格式化（Issue #3044 / #3045）。

10 位秒级时间戳被当成毫秒会在 TTY 显示 1970 年；`activeTime` 缺失文案时
原样打印 13 位毫秒数。两者都只影响终端展示，JSON 信封保留平台原值。
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands.chat import _format_ts
from boss_agent_cli.commands.recruiter._render import candidate_list_rows
from boss_agent_cli.main import cli
from boss_agent_cli.pipeline_state import _ts_to_label
from boss_agent_cli.services.chat_utils import MIN_PLAUSIBLE_TS_MS, safe_local_time
from boss_agent_cli.services.recruiter_chat_summary import format_chat_time

SECONDS_TS = 1_700_000_000
MILLIS_TS = 1_700_000_000_000


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 120) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	return CliRunner().invoke(cli, ["--role", "recruiter", "--data-dir", str(data_dir), *args])


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: (
		response.get("zpData") if "zpData" in response else response.get("data")
	)
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


# ── 时间下界（#3044）────────────────────────────────────────────────


def test_seconds_timestamp_is_not_drawn_as_1970() -> None:
	assert safe_local_time(SECONDS_TS) is None
	assert format_chat_time(SECONDS_TS) is None
	assert format_chat_time(str(SECONDS_TS)) is None
	assert _format_ts(SECONDS_TS) == "-"
	assert _ts_to_label(SECONDS_TS) == "-"


def test_tiny_and_bool_values_are_unknown() -> None:
	assert format_chat_time(123) is None
	assert format_chat_time(True) is None
	assert safe_local_time(float("nan")) is None


def test_lower_bound_and_millisecond_values_still_work() -> None:
	boundary = safe_local_time(MIN_PLAUSIBLE_TS_MS)
	assert boundary is not None and boundary.year == 2000
	assert format_chat_time(MILLIS_TS) is not None
	assert _format_ts(MILLIS_TS) != "-"


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_hr_applications_seconds_last_ts_is_shown_as_unknown(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"code": 200,
		"data": {
			"friendList": [
				{
					"name": "候选人甲",
					"brandName": "示例公司",
					"jobName": "岗位A",
					"lastMsg": "hi",
					"lastTS": SECONDS_TS,
				},
			],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "applications")
	assert result.exit_code == 0, result.output or repr(result.exception)
	out = stream.getvalue()
	assert "候选人甲" in out
	assert "01-21" not in out
	parsed = json.loads(_invoke(tmp_path, "--json", "hr", "applications").stdout)
	assert parsed["data"]["friendList"][0]["lastTS"] == SECONDS_TS


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_hr_chatmsg_seconds_create_time_is_shown_as_unknown(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.chat_history.return_value = {
		"code": 200,
		"data": {
			"messageList": [{"from": {"type": "我"}, "text": "hi", "createTime": SECONDS_TS}],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "chatmsg", "12345")
	assert result.exit_code == 0, result.output or repr(result.exception)
	out = stream.getvalue()
	assert "hi" in out
	assert "01-21" not in out


# ── activeTime 数字不再原样打印（#3045）────────────────────────────


def test_active_time_number_renders_readable_time() -> None:
	rows = candidate_list_rows(
		{
			"geekList": [
				{"name": "候选人甲", "activeTime": MILLIS_TS},
				{"name": "候选人乙", "activeTimeDesc": "今日活跃", "activeTime": MILLIS_TS},
				{"name": "候选人丙", "activeTime": SECONDS_TS},
			],
		}
	)
	assert rows[0]["active"] == format_chat_time(MILLIS_TS)
	assert rows[1]["active"] == "今日活跃"
	assert rows[2]["active"] is None


@patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager")
def test_hr_recommendations_active_time_does_not_print_raw_millis(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.recommend_geeks.return_value = {
		"code": 200,
		"data": {
			"geekList": [{"name": "候选人丁", "expectPosition": "Go 工程师", "activeTime": 1_764_300_000_000}],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "recommendations", "--job-id", "job-demo")
	assert result.exit_code == 0, result.output or repr(result.exception)
	out = stream.getvalue()
	assert "候选人丁" in out
	assert "1764300000000" not in out
	parsed = json.loads(_invoke(tmp_path, "--json", "hr", "recommendations", "--job-id", "job-demo").stdout)
	assert parsed["data"]["geekList"][0]["activeTime"] == 1_764_300_000_000
