"""`boss chat --raw` 的契约（Issue #2250）。

外部程序归档沟通列表时需要原始联系人对象：摘要字段会把 ``relationType``
等编码翻译成文案、丢掉未投影的键。``--raw`` 只在普通输出里附加原始对象，
筛选、分页与导出 / 快照行为保持原样。
"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli

_MODULE = "boss_agent_cli.commands.chat"


def _friend_item(*, uid=10001, name="联系人甲", relation=1):
	return {
		"uid": uid,
		"securityId": f"sid_{uid}",
		"name": name,
		"brandName": "示例公司",
		"title": "招聘专员",
		"lastMsg": "你好",
		"lastTS": 1700000000000,
		"unreadMsgCount": 3,
		"relationType": relation,
		"lastMessageInfo": {"status": 1, "timestamp": 1700000000000, "extra": "keep-me"},
	}


def _response(items, *, has_more=None):
	data = {"result": items, "friendList": items}
	if has_more is not None:
		data["hasMore"] = has_more
	return {"code": 0, "zpData": data}


def _invoke(tmp_path, *argv, items=None, has_more=None):
	friends = items if items is not None else [_friend_item()]
	with patch(f"{_MODULE}.get_platform_instance") as platform_cls, patch(f"{_MODULE}.AuthManager"):
		client = platform_cls.return_value
		client.__enter__ = lambda self: self
		client.__exit__ = lambda self, *args: None
		client.unwrap_data.side_effect = lambda response: response.get("zpData")
		client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
		client.friend_list.return_value = _response(friends, has_more=has_more)
		return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "chat", *argv])


def _data(result):
	assert result.exit_code == 0, result.output
	return json.loads(result.output)["data"]


def test_raw_attaches_original_contact_without_touching_summary(tmp_path):
	items = [_friend_item(uid=10001), _friend_item(uid=10002, name="联系人乙", relation=2)]

	plain = _data(_invoke(tmp_path, items=items))
	with_raw = _data(_invoke(tmp_path, "--raw", items=items))

	assert [record["raw"] for record in plain if "raw" in record] == []
	# 摘要字段逐条一致，raw 只在附加字段里。
	for summarized, enriched in zip(plain, with_raw):
		assert {key: value for key, value in enriched.items() if key != "raw"} == summarized
	assert [record["raw"] for record in with_raw] == items


def test_raw_preserves_codes_and_unprojected_keys(tmp_path):
	item = _friend_item(relation=99)
	record = _data(_invoke(tmp_path, "--raw", items=[item]))[0]

	assert record["initiated_by"] == "未知"
	assert record["raw"]["relationType"] == 99
	assert record["raw"]["lastMessageInfo"]["extra"] == "keep-me"


def test_raw_keeps_filter_and_order_alignment(tmp_path):
	items = [
		_friend_item(uid=10001, relation=1),
		_friend_item(uid=10002, relation=2),
		_friend_item(uid=10003, relation=1),
	]
	records = _data(_invoke(tmp_path, "--raw", "--from", "me", items=items))

	assert [record["uid"] for record in records] == [10002]
	assert records[0]["raw"]["uid"] == 10002


def test_raw_does_not_change_pagination(tmp_path):
	items = [_friend_item(uid=10001), _friend_item(uid=10002)]
	plain = json.loads(_invoke(tmp_path, items=items, has_more=True).output)
	with_raw = json.loads(_invoke(tmp_path, "--raw", items=items, has_more=True).output)

	assert with_raw["data"] != plain["data"]
	assert with_raw["pagination"] == plain["pagination"]
	next_pages = [action for action in with_raw["hints"]["next_actions"] if "--page" in action]
	assert len(next_pages) == 1
	assert "--raw" in next_pages[0].split()


def test_next_page_hint_keeps_raw_and_filters(tmp_path):
	result = _invoke(tmp_path, "--raw", "--from", "boss", "--days", "7", items=[_friend_item()], has_more=True)
	parsed = json.loads(result.output)
	next_page = [action for action in parsed["hints"]["next_actions"] if "--page 2" in action][0]

	argv = next_page.split()
	assert "--raw" in argv
	assert "--from" in argv and "boss" in argv
	assert "--days" in argv and "7" in argv


def test_export_ignores_raw(tmp_path):
	target = tmp_path / "export.json"
	result = _invoke(tmp_path, "--raw", "--export", "json", "-o", str(target), items=[_friend_item(relation=99)])

	assert result.exit_code == 0, result.output
	exported = json.loads(target.read_text(encoding="utf-8"))
	assert "raw" not in json.dumps(exported, ensure_ascii=False)

	snapshots = sorted((tmp_path / "chat-history").glob("*.json"))
	assert snapshots
	for snapshot in snapshots:
		assert "raw" not in snapshot.read_text(encoding="utf-8")


def test_non_object_entries_do_not_shift_raw_correspondence(tmp_path):
	items = [_friend_item(uid=10001), "junk", _friend_item(uid=10002)]

	records = _data(_invoke(tmp_path, "--raw", items=items))

	assert [record["uid"] for record in records] == [10001, 10002]
	assert [record["raw"]["uid"] for record in records] == [10001, 10002]


def test_raw_is_absent_by_default_on_snapshot_records(tmp_path):
	target = tmp_path / "export.json"
	_invoke(tmp_path, "--export", "json", "-o", str(target), items=[_friend_item()])

	snapshot = Path(sorted((tmp_path / "chat-history").glob("*.json"))[0])
	assert "raw" not in snapshot.read_text(encoding="utf-8")


def test_mcp_chat_forwards_raw():
	from boss_agent_cli.mcp_args import _build_args

	args = _build_args("chat", {"raw": True, "page": 2, "from_who": "boss"})

	assert "--raw" in args
	assert args[:2] == ["chat", "--from"]


def test_mcp_chat_defaults_to_summary_only():
	from boss_agent_cli.mcp_args import _build_args

	assert "--raw" not in _build_args("chat", {"page": 1})


def test_mcp_schema_declares_raw():
	from boss_agent_cli.mcp_tools import TOOLS

	tool = next(tool for tool in TOOLS if tool.name == "boss_chat")
	assert tool.input_schema["properties"]["raw"]["type"] == "boolean"
