"""自动化队列文本字段与在途标记的类型漂移（Issue #1867 #1868）。"""

from pathlib import Path

import pytest

from boss_agent_cli.automation.execution import _inflight_needs_notification
from boss_agent_cli.automation.storage import (
	AutomationStateError,
	_pending_from_row,
	_review_from_row,
)

PATH = Path("queue.jsonl")


def _row_for(parser, **overrides: object) -> dict[str, object]:
	status = "pending" if parser is _pending_from_row else "review"
	return _row(status=status, **overrides)


def _row(**overrides: object) -> dict[str, object]:
	row: dict[str, object] = {
		"id": "r1",
		"ts": "2026-11-19T00:00:00",
		"platform": "zhipin",
		"candidate_key": "c1",
		"action": "send_follow_up",
		"status": "pending",
		"confidence": 0.9,
		"reason": "ok",
		"message": "hi",
	}
	row.update(overrides)
	return row


# ── Issue #1867：非字符串文本字段按状态损坏上报，不再 str() 成话术 ──


@pytest.mark.parametrize("parser", [_pending_from_row, _review_from_row], ids=["pending", "review"])
@pytest.mark.parametrize("key", ["message", "platform", "candidate_key", "reason"])
@pytest.mark.parametrize("value", [None, 123, {}, [], True])
def test_non_string_text_fields_fail_closed(parser, key: str, value: object) -> None:
	with pytest.raises(AutomationStateError):
		parser(_row_for(parser, **{key: value}), PATH)


@pytest.mark.parametrize("parser", [_pending_from_row, _review_from_row], ids=["pending", "review"])
def test_missing_text_fields_default_to_empty(parser) -> None:
	row = _row_for(parser)
	for key in ("platform", "candidate_key", "reason", "message"):
		row.pop(key, None)

	item = parser(row, PATH)
	assert item.message == ""
	assert item.platform == ""
	assert item.candidate_key == ""
	assert item.reason == ""


@pytest.mark.parametrize("parser", [_pending_from_row, _review_from_row], ids=["pending", "review"])
def test_string_text_fields_pass_through(parser) -> None:
	item = parser(_row_for(parser), PATH)

	assert item.message == "hi"
	assert item.platform == "zhipin"
	assert item.reason == "ok"


# ── Issue #1868：只有布尔 True 才算已通知 ───────────────────────────


@pytest.mark.parametrize("notified", [0, "", "false", "true", 1, None, [], {}])
def test_non_boolean_true_notified_requires_renotification(notified: object) -> None:
	prior = {"inflight_action": {"status": "verification-required", "notified": notified}}

	assert _inflight_needs_notification(prior) is True


def test_boolean_false_requires_renotification() -> None:
	prior = {"inflight_action": {"status": "verification-required", "notified": False}}

	assert _inflight_needs_notification(prior) is True


def test_boolean_true_notified_is_respected() -> None:
	prior = {"inflight_action": {"status": "verification-required", "notified": True}}

	assert _inflight_needs_notification(prior) is False


def test_missing_marker_keeps_legacy_semantics() -> None:
	assert _inflight_needs_notification({"inflight_action": {"status": "verification-required"}}) is False
	assert _inflight_needs_notification({"inflight_action": {}}) is False
	assert _inflight_needs_notification({}) is False
	assert _inflight_needs_notification({"inflight_action": "verification-required"}) is False
