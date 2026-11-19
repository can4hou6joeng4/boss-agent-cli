"""审计批次：reject 清理遗留 pending、allowed_actions 非字符串元素（Issue #2124、#2125）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.models import PendingAction, PlatformAction, ReviewItem
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore

_TS = "2026-11-20T00:00:00+00:00"
_REVIEWED_AT = "2026-11-20T00:02:00+00:00"


def _seed_review(store: AutomationStore, review_id: str = "review-1") -> None:
	store.append_review(
		ReviewItem(
			id=review_id,
			ts=_TS,
			platform="zhipin",
			candidate_key="候选人A",
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="review",
			confidence=0.7,
			reason="needs review",
			message="您好，想确认下近期是否看机会？",
		)
	)


def _pending_row(review_id: str = "review-1", *, status: str = "pending") -> PendingAction:
	return PendingAction(
		id=review_id,
		ts=_TS,
		platform="zhipin",
		candidate_key="候选人A",
		action=PlatformAction.SEND_QUESTIONNAIRE.value,
		status=status,
		confidence=0.7,
		reason="needs review",
		message="您好，想确认下近期是否看机会？",
		approved_review_id=review_id,
	)


# ── #2124：reject 处理 approve 中断遗留的 pending 行 ─────────────────


def test_reject_removes_leftover_pending_row(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.append_pending(_pending_row())

	rejected = store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert rejected is not None
	assert rejected.status == "rejected"
	assert store.read_pending() == []


@pytest.mark.parametrize("status", ["executing", "verification-required"])
def test_reject_refuses_inflight_pending_row(tmp_path: Path, status: str) -> None:
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.append_pending(_pending_row(status=status))

	with pytest.raises(AutomationStateError):
		store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert [item.status for item in store.read_reviews()] == ["review"]
	assert [item.status for item in store.read_pending()] == [status]


def test_reject_keeps_executed_history(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.append_pending(_pending_row(status="executed"))

	rejected = store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert rejected is not None
	assert [item.status for item in store.read_pending()] == ["executed"]


def test_reject_ignores_pending_rows_of_other_reviews(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_review(store, "review-1")
	_seed_review(store, "review-2")
	store.append_pending(_pending_row("review-1"))
	store.append_pending(_pending_row("review-2"))

	store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert [(item.id, item.status) for item in store.read_pending()] == [("review-2", "pending")]


def test_reject_without_linked_pending_stays_unchanged(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_review(store)

	rejected = store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert rejected is not None
	assert rejected.status == "rejected"
	assert store.read_pending() == []


# ── #2125：allowed_actions 非字符串元素按配置错误上报 ────────────────


@pytest.mark.parametrize("bad", [["x"], {"a": 1}, 1, None])
def test_allowed_actions_container_element_is_config_error(bad: object) -> None:
	with pytest.raises(ValueError) as excinfo:
		automation_config_from_dict({"allowed_actions": [bad]})

	assert "未知动作" in str(excinfo.value)


def test_allowed_actions_unknown_string_still_rejected() -> None:
	with pytest.raises(ValueError, match="未知动作"):
		automation_config_from_dict({"allowed_actions": ["no-such-action"]})


def test_allowed_actions_valid_list_parses() -> None:
	config = automation_config_from_dict({"allowed_actions": ["send_questionnaire"]})

	assert [action.value for action in config.allowed_actions] == ["send_questionnaire"]
