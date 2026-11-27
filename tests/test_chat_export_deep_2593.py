"""聊天导出 / 快照的单条深嵌套字段降级回归（Issue #2593）。"""

import datetime
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services import chat_snapshot as snapshot_module
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff
from boss_agent_cli.services.json_files import degrade_unserializable_records


def _deep_value() -> Any:
	"""迭代构造深层容器：不依赖各 Python 版本 json.loads 的递归上限。"""
	value: Any = []
	for _ in range(1500):
		value = [value]
	return value


def _friend(uid: int, name: str, last_msg: Any) -> dict[str, Any]:
	return {
		"uid": uid,
		"security_id": f"sid_{uid}",
		"name": name,
		"last_msg": last_msg,
		"lastMessageInfo": {"status": 2},
		"unread": 0,
	}


def test_render_export_json_degrades_only_bad_field():
	friends = [_friend(1, "正常联系人", "你好"), _friend(2, "异常联系人", _deep_value())]

	exported = json.loads(render_export(friends, "json", None, None, {}))

	assert len(exported) == 2
	assert exported[0] == friends[0]
	bad = exported[1]
	assert bad["uid"] == 2
	assert bad["name"] == "异常联系人"
	assert "last_msg" not in bad
	assert bad["lastMessageInfo"] == {"status": 2}
	assert "嵌套" in bad["export_error"]


def test_snapshot_degrades_bad_field_without_losing_completeness(tmp_path, monkeypatch):
	snapshot_dir = tmp_path / "chat-history"
	friends = [_friend(1, "正常联系人", "你好"), _friend(2, "异常联系人", _deep_value())]
	dates = iter([datetime.date(2026, 11, 22), datetime.date(2026, 11, 23)])
	monkeypatch.setattr(
		snapshot_module,
		"datetime",
		SimpleNamespace(
			date=SimpleNamespace(
				today=lambda: next(dates),
				fromisoformat=datetime.date.fromisoformat,
			)
		),
	)

	first = save_snapshot_and_diff(str(snapshot_dir), friends, MagicMock(), complete=True)
	second = save_snapshot_and_diff(str(snapshot_dir), friends, MagicMock(), complete=True)

	path = snapshot_dir / "2026-11-22.json"
	stored = json.loads(path.read_text(encoding="utf-8"))
	assert len(stored) == 2
	bad = next(item for item in stored if item["uid"] == 2)
	assert "last_msg" not in bad
	assert bad["lastMessageInfo"] == {"status": 2}
	assert "嵌套" in bad["snapshot_error"]
	meta = json.loads((snapshot_dir / "2026-11-22.meta").read_text(encoding="utf-8"))
	assert meta["complete"] is True
	assert first["complete"] is True
	assert second["is_first"] is False
	assert second["removed"] == []


@pytest.mark.parametrize("fmt", ["md", "csv", "json", "html"])
def test_chat_export_all_formats_survive_deep_contact_field(tmp_path, fmt):
	items = [
		{
			"uid": 1,
			"securityId": "sid_1",
			"name": "正常联系人",
			"lastMsg": "你好",
			"lastTS": 1700000000000,
			"unreadMsgCount": 0,
			"relationType": 2,
			"lastMessageInfo": {"status": 2},
		},
		{
			"uid": 2,
			"securityId": "sid_2",
			"name": "异常联系人",
			"lastMsg": _deep_value(),
			"lastTS": 1700000000000,
			"unreadMsgCount": 0,
			"relationType": 2,
			"lastMessageInfo": {"status": 2},
		},
	]
	target = tmp_path / f"export.{fmt}"
	with patch("boss_agent_cli.commands.chat.get_platform_instance") as platform_cls, \
			patch("boss_agent_cli.commands.chat.AuthManager"):
		client = platform_cls.return_value
		client.__enter__ = lambda self: self
		client.__exit__ = lambda self, *args: None
		client.unwrap_data.side_effect = lambda response: response.get("zpData")
		client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
		client.friend_list.return_value = {
			"code": 0,
			"zpData": {"result": items, "friendList": items, "hasMore": False, "page": 1},
		}
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "chat", "--export", fmt, "-o", str(target)],
		)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 2
	assert target.is_file()
	content = target.read_text(encoding="utf-8")
	assert "正常联系人" in content
	assert "异常联系人" in content
	if fmt == "json":
		rows = json.loads(content)
		assert len(rows) == 2
		# CLI 在 _friend_record 边界就把容器字段归一为 "-"，导出器不必再走
		# 单条降级；render_export 自身的降级由
		# test_render_export_json_degrades_only_bad_field 覆盖（Issue #3075）。
		assert rows[1]["last_msg"] == "-"
	assert envelope["data"]["diff"]["complete"] is True


def test_shared_degradation_keeps_serializable_nested_fields():
	records = [
		"不是对象",
		{
			"uid": 3,
			"deep": _deep_value(),
			"normal": {"status": 2},
		},
	]

	degraded = degrade_unserializable_records(records, marker="问题", message="嵌套过深")

	assert degraded[0] == {"问题": "该条记录不是对象，已省略原始内容"}
	assert degraded[1]["uid"] == 3
	assert degraded[1]["normal"] == {"status": 2}
	assert "deep" not in degraded[1]
	assert degraded[1]["问题"] == "嵌套过深"


def test_snapshot_falls_back_on_whole_write_serialization_failure(tmp_path):
	"""整体写失败后必须重写降级版；单条试写的递归余量与整份落盘不同。"""
	snapshot_dir = tmp_path / "chat-history"
	friends = [{"uid": "u", "name": "联系人", "bad": {1, 2}}]

	save_snapshot_and_diff(str(snapshot_dir), friends, MagicMock(), complete=True)

	path = snapshot_dir / f"{datetime.date.today().isoformat()}.json"
	stored = json.loads(path.read_text(encoding="utf-8"))
	assert len(stored) == 1
	assert stored[0]["uid"] == "u"
	assert "bad" not in stored[0]
	assert "snapshot_error" in stored[0]


def test_render_export_json_filters_non_objects_consistently():
	clean = json.loads(render_export([None, {"uid": 1}], "json", None, None, {}))
	with_bad = json.loads(
		render_export([None, {"uid": 1, "deep": _deep_value()}], "json", None, None, {})
	)

	assert clean == [{"uid": 1}]
	assert len(with_bad) == 1
	assert with_bad[0]["uid"] == 1
	assert "export_error" in with_bad[0]
