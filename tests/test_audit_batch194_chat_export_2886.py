"""Issue #2886：聊天导出的未读口径与嵌套链接提取。"""

from boss_agent_cli.services.chat_export import prepare_render_data, render_export
from boss_agent_cli.services.chat_snapshot import _unread_count
from boss_agent_cli.services.chat_utils import normalize_chat_message


def _friend(unread):
	return {
		"uid": "u1", "name": "A", "msg_status": "未读",
		"initiated_by": "我主动", "unread": unread,
	}


def test_negative_unread_is_treated_as_zero_like_snapshot_diff():
	friend = _friend(-1)
	row = prepare_render_data([friend], None, {})["sections"][0]["rows"][0]

	assert row["unread"] == 0
	assert _unread_count(friend) == 0
	assert "'-1" not in render_export([friend], "csv", None, None, {})


def test_positive_unread_keeps_existing_behavior():
	friend = _friend(3)
	row = prepare_render_data([friend], None, {})["sections"][0]["rows"][0]

	assert row["unread"] == 3
	assert "'-1" not in render_export([friend], "csv", None, None, {})


def test_links_are_extracted_from_nested_lists():
	url = "https://example.com/deep"
	flat = normalize_chat_message(
		{"from": {"uid": "1"}, "body": {"text": url}},
		gid="1", friend_name="n", include_raw=True,
	)["links"]
	nested = normalize_chat_message(
		{"from": {"uid": "1"}, "body": {"content": [[{"text": url}]]}},
		gid="1", friend_name="n", include_raw=True,
	)["links"]
	deeper = normalize_chat_message(
		{"from": {"uid": "1"}, "body": {"content": [[{"parts": [[{"text": url}]]}]]}},
		gid="1", friend_name="n", include_raw=True,
	)["links"]

	assert flat == [url]
	assert nested == [url]
	assert deeper == [url]
