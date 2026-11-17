"""schema 描述与实现一致性（Issue #1505、#1506）。

- `chat -o` 的默认目录必须与 `commands/chat.py` 的回退路径一致，并说明只在
  `--export` 时生效；
- `mark --label` 的 enum 必须覆盖实现 `_LABEL_MAP` 的全部标签，并说明也接受
  数字 ID。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.commands.mark import _LABEL_MAP
from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA


def _native_schema_data() -> dict:
	result = CliRunner().invoke(cli, ["schema"])
	assert result.exit_code == 0
	return json.loads(result.output)["data"]


def test_chat_output_description_matches_implementation_fallback():
	"""schema 不能再声明不存在的 ~/Documents/files/boss 默认目录。"""
	description = SCHEMA_DATA["commands"]["chat"]["options"]["-o, --output"]["description"]

	assert DEFAULTS["export_dir"] is None
	assert "chat-export" in description
	assert "config.export_dir" in description
	assert "--export" in description
	assert "Documents/files/boss" not in description

	schema_text = json.dumps(_native_schema_data(), ensure_ascii=False)
	assert "chat-export" in schema_text
	assert "Documents/files/boss" not in schema_text


def test_chat_help_matches_schema_description():
	"""`boss chat --help` 与 schema 用同一套默认路径说明。"""
	result = CliRunner().invoke(cli, ["chat", "--help"])

	assert result.exit_code == 0
	assert "chat-export" in result.output
	assert "--export 时生效" in result.output
	assert "Documents/files/boss" not in result.output


def test_chat_export_without_config_uses_data_dir_fallback(tmp_path):
	"""未配置 export_dir 时实际写入 <data-dir>/chat-export，与描述一致。"""
	with patch("boss_agent_cli.commands.chat.get_platform_instance") as platform_cls, \
		patch("boss_agent_cli.commands.chat.AuthManager"):
		instance = platform_cls.return_value
		instance.__enter__ = lambda self: self
		instance.__exit__ = lambda self, *args: False
		instance.is_success.return_value = True
		instance.unwrap_data.side_effect = lambda response: response.get("zpData")
		instance.friend_list.return_value = {"zpData": {"result": [], "hasMore": False}}

		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "chat", "--export", "json"]
		)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	expected_dir = tmp_path / "chat-export"
	assert Path(parsed["data"]["path"]).parent == expected_dir
	assert parsed["data"]["path"].endswith(".json")
	assert list(expected_dir.glob("沟通列表-*.json"))


def test_mark_label_enum_covers_implementation():
	label = SCHEMA_DATA["commands"]["mark"]["options"]["--label"]

	assert set(label["enum"]) == set(_LABEL_MAP)
	assert "牛人发起" in label["enum"]
	assert "数字 ID" in label["description"]

	help_result = CliRunner().invoke(cli, ["mark", "--help"])
	assert help_result.exit_code == 0
	assert "牛人发起" in help_result.output


def test_mark_label_niuren_faqi_is_executable(tmp_path):
	"""schema 枚举里的 `牛人发起` 必须真的能通过 CLI 走到平台写入。"""
	friend = {
		"uid": 10001,
		"securityId": "sid_current",
		"name": "联系人甲",
		"friendSource": 0,
	}
	with patch("boss_agent_cli.commands.mark.get_platform_instance") as platform_cls, \
		patch("boss_agent_cli.commands.mark.AuthManager"):
		instance = platform_cls.return_value
		instance.__enter__ = lambda self: self
		instance.__exit__ = lambda self, *args: False
		instance.is_success.return_value = True
		instance.unwrap_data.side_effect = lambda response: response.get("zpData")
		instance.friend_list.return_value = {"zpData": {"result": [friend], "hasMore": False}}
		instance.friend_label.return_value = {"zpData": {}}

		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "mark", "10001", "--label", "牛人发起"],
		)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["label"] == "牛人发起"
	assert instance.friend_label.call_args.args[:3] == ("10001", 8, 0)
