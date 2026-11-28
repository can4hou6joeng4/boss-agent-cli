"""chatmsg 非直读路径的下一页提示必须可原样重放（Issue #3151）。"""

import json
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


class FakePlatform:
	name = "zhipin"

	def __enter__(self) -> "FakePlatform":
		return self

	def __exit__(self, *args: Any) -> None:
		return None

	def is_success(self, result: dict[str, Any]) -> bool:
		return result.get("code", 0) == 0

	def unwrap_data(self, result: dict[str, Any]) -> Any:
		return result.get("zpData")

	def friend_list(self, *, page: int) -> dict[str, Any]:
		return {
			"code": 0,
			"zpData": {
				"result": [{"uid": "-uid-1", "securityId": "sid-1", "name": "demo"}],
				"hasMore": False,
			},
		}

	def chat_history(self, gid: str, sid: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		return {
			"code": 0,
			"zpData": {
				"page": 1,
				"hasMore": True,
				"messages": [
					{
						"id": 1,
						"from": {"uid": "-uid-1"},
						"type": 1,
						"text": "hi",
						"time": 1700000000000,
					}
				],
			},
		}


def _invoke(data_dir: Path, args: list[str]):
	with (
		patch("boss_agent_cli.commands.chatmsg.AuthManager"),
		patch("boss_agent_cli.commands.chatmsg.get_platform_instance", return_value=FakePlatform()),
	):
		return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def _next_page_hint(envelope: dict[str, Any]) -> str:
	return [action for action in envelope["hints"]["next_actions"] if "chatmsg" in action][-1]


def _replay_args(hint: str) -> list[str]:
	argv = shlex.split(hint)
	return argv[argv.index("chatmsg") + 1 :]


def test_negative_uid_next_page_is_replayable(tmp_path: Path) -> None:
	first = json.loads(_invoke(tmp_path, ["chatmsg", "--", "-uid-1"]).stdout)

	assert _next_page_hint(first).endswith("chatmsg --page 2 --count 20 -- -uid-1")
	replay = _invoke(tmp_path, ["chatmsg", *_replay_args(_next_page_hint(first))])
	assert replay.exit_code == 0, replay.stdout
	assert json.loads(replay.stdout)["ok"] is True


def test_negative_uid_next_page_keeps_raw_before_separator(tmp_path: Path) -> None:
	first = json.loads(_invoke(tmp_path, ["chatmsg", "--raw", "--", "-uid-1"]).stdout)

	assert _next_page_hint(first).endswith("chatmsg --page 2 --count 20 --raw -- -uid-1")
	replay = _invoke(tmp_path, ["chatmsg", *_replay_args(_next_page_hint(first))])
	assert replay.exit_code == 0, replay.stdout
	assert json.loads(replay.stdout)["ok"] is True
