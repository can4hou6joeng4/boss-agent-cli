"""越界时间戳与 pipeline 聚合的类型漂移边界（Issue #1295 / #1297 / #1298）。

平台把纳秒时间戳当毫秒传来（``10**17``）、或 JSON 的 ``1e400`` 变成 ``inf`` 时，
``datetime.fromtimestamp`` 抛 ``ValueError`` / ``OverflowError``；混合类型的
``brandName`` 会让排序跨类型比较抛 ``TypeError``。这些都是确定性坏数据，
原先会落成可重试的 ``NETWORK_ERROR`` 或 ``INTERNAL_ERROR``。
"""

from boss_agent_cli.commands.chat import _format_ts
from boss_agent_cli.commands.chatmsg import _normalize_message
from boss_agent_cli.services.recruiter_chat_summary import format_chat_time
from boss_agent_cli.pipeline_state import _ts_to_label, build_pipeline_items, now_ts_or_clock

_OVERLONG_INT = "9" * 4301


# ── 时间格式化：坏值按「无时间」输出 ────────────────────────────────


def test_chat_format_ts_rejects_out_of_range_values():
	assert _format_ts(10**17) == "-"
	assert _format_ts(10**400) == "-"
	assert _format_ts(float("inf")) == "-"


def test_chat_format_ts_keeps_normal_values():
	assert _format_ts(1_700_000_000_000).count(":") == 1


def test_chatmsg_normalize_message_drops_out_of_range_time():
	assert _normalize_message({"time": 10**17, "text": "hi"}, gid="1", friend_name="x")["time"] == ""
	assert _normalize_message({"time": float("inf"), "text": "hi"}, gid="1", friend_name="x")["time"] == ""


def test_recruiter_chat_time_rejects_out_of_range_values():
	assert format_chat_time(10**17) is None
	assert format_chat_time(float("inf")) is None
	# 超过 4300 位的数字串 int() 抛 ValueError：原样展示平台字符串。
	assert format_chat_time(_OVERLONG_INT) == _OVERLONG_INT


def test_pipeline_ts_label_rejects_out_of_range_values():
	assert _ts_to_label(10**17) == "-"
	assert _ts_to_label(float("inf")) == "-"


# ── pipeline 聚合：混合类型字段不拖垮整份结果 ──────────────────────


def test_pipeline_sort_tolerates_mixed_company_types():
	items = build_pipeline_items(
		chat_items=[
			{"brandName": 123, "title": "a", "relationType": 1, "securityId": "s1"},
			{"brandName": "abc", "title": "b", "relationType": 1, "securityId": "s2"},
		],
		interview_items=[],
		now_ts_ms=1_700_000_000_000,
	)

	assert [item["security_id"] for item in items] == ["s1", "s2"]


# ── --now-ts-ms 0 是显式值 ─────────────────────────────────────────


def test_now_ts_zero_is_an_explicit_value():
	assert now_ts_or_clock(0) == 0
	assert now_ts_or_clock(None) > 0
