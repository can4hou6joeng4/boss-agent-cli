"""Issue #3079：`mark` 成功信封的 security_id 与 friend_security_id 同口径。

`friend_item.get("securityId") or friend_item.get("security_id")` 在 camelCase
是「真值但不可用」（容器 / 非 bool 数字 / 纯空白字符串）时已选中它，随后的
``isinstance(str)`` 判空，合法的 snake_case 回退被吞掉；命令报 ok=true 却给出
不能用于 detail / apply 的空标识。与 Issue #2723（services 层）、#3075（chat
信封）同一缺陷族。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls: Any) -> Any:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = (
		lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	)
	return instance


def _invoke(tmp_path: Any, *args: str) -> Any:
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _mark_with_friend(tmp_path: Any, friend_item: dict[str, Any]) -> Any:
	base = {"uid": "1", "name": "候选人A"}
	with patch("boss_agent_cli.commands.mark.get_platform_instance") as platform_cls, patch(
		"boss_agent_cli.commands.mark.AuthManager"
	):
		platform = _ctx_mock(platform_cls)
		platform.friend_list.return_value = {"zpData": {"result": [{**base, **friend_item}]}}
		return _invoke(tmp_path, "mark", "1", "--label", "收藏")


def test_mark_falls_back_to_snake_case_when_camel_is_container(tmp_path) -> None:
	"""securityId 漂移成对象时，合法的 security_id 回退不能被吞掉。"""
	result = _mark_with_friend(tmp_path, {"securityId": {"a": 1}, "security_id": "sec-fallback"})

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == "sec-fallback"


def test_mark_falls_back_to_snake_case_when_camel_is_blank(tmp_path) -> None:
	"""纯空白 camelCase 与缺失同口径，继续读 snake_case 回退。"""
	result = _mark_with_friend(tmp_path, {"securityId": "   ", "security_id": "sec-fallback"})

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == "sec-fallback"


def test_mark_never_echoes_drifted_snake_case_either(tmp_path) -> None:
	"""两个键都不可用时仍为空串：不能把容器 / 数字 repr 当令牌回显。"""
	result = _mark_with_friend(tmp_path, {"securityId": ["x"], "security_id": 123})

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == ""


def test_mark_keeps_trimmed_camel_case(tmp_path) -> None:
	"""camelCase 为合法字符串时按既有契约 strip 后回显。"""
	result = _mark_with_friend(tmp_path, {"securityId": " sec-9 ", "security_id": "sec-other"})

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"]["security_id"] == "sec-9"
