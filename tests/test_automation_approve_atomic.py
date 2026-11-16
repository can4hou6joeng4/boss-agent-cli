"""复核批准的可重试提交顺序（Issue #1248）。

`approve_review` 必须先入队再标记复核项：任何一步失败后重试都不能让已批准的
动作丢失，也不能重复入队。
"""

import pytest

from boss_agent_cli.automation.models import PendingAction, PlatformAction, ReviewItem
from boss_agent_cli.automation.storage import AutomationStore


def _seed_review(store: AutomationStore, review_id: str = "review-1") -> None:
	store.append_review(
		ReviewItem(
			id=review_id,
			ts="2026-11-16T00:00:00+00:00",
			platform="zhipin",
			candidate_key="候选人A",
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="review",
			confidence=0.7,
			reason="needs review",
			message="您好，想确认下近期是否看机会？",
		)
	)


def _review_statuses(store: AutomationStore) -> list[tuple[str, str]]:
	return [(item.id, item.status) for item in store.read_reviews()]


def _pending_statuses(store: AutomationStore) -> list[tuple[str, str, str]]:
	return [(item.id, item.status, item.approved_review_id) for item in store.read_pending()]


def test_approve_queues_before_marking_review(tmp_path):
	store = AutomationStore(tmp_path)
	_seed_review(store)
	seen: list[list[tuple[str, str]]] = []
	real_append = store.append_pending

	def inspect(action: PendingAction) -> None:
		seen.append(_review_statuses(store))
		real_append(action)

	store.append_pending = inspect

	pending = store.approve_review("review-1", "2026-11-16T00:01:00+00:00")

	assert pending is not None
	assert seen == [[("review-1", "review")]]
	assert _review_statuses(store) == [("review-1", "approved")]
	assert _pending_statuses(store) == [("review-1", "pending", "review-1")]


def test_failed_enqueue_leaves_review_actionable(tmp_path):
	store = AutomationStore(tmp_path)
	_seed_review(store)
	real_append = store.append_pending

	def fail(action: PendingAction) -> None:
		raise OSError("disk full")

	store.append_pending = fail
	with pytest.raises(OSError):
		store.approve_review("review-1", "2026-11-16T00:01:00+00:00")

	assert _review_statuses(store) == [("review-1", "review")]
	assert _pending_statuses(store) == []

	store.append_pending = real_append
	pending = store.approve_review("review-1", "2026-11-16T00:02:00+00:00")

	assert pending is not None
	assert pending.id == "review-1"
	assert _review_statuses(store) == [("review-1", "approved")]
	assert _pending_statuses(store) == [("review-1", "pending", "review-1")]


def test_failed_review_write_is_retryable_without_duplicate_enqueue(tmp_path):
	store = AutomationStore(tmp_path)
	_seed_review(store)
	real_write_reviews = store.write_reviews

	def fail(items):
		raise OSError("disk full")

	store.write_reviews = fail
	with pytest.raises(OSError):
		store.approve_review("review-1", "2026-11-16T00:01:00+00:00")

	# 入队已成功、复核项仍是 review：重试只补写复核队列，不重复入队。
	assert _review_statuses(store) == [("review-1", "review")]
	assert _pending_statuses(store) == [("review-1", "pending", "review-1")]

	store.write_reviews = real_write_reviews
	pending = store.approve_review("review-1", "2026-11-16T00:02:00+00:00")

	assert pending is not None
	assert pending.id == "review-1"
	assert _review_statuses(store) == [("review-1", "approved")]
	assert _pending_statuses(store) == [("review-1", "pending", "review-1")]


def test_existing_pending_row_is_not_duplicated_on_retry(tmp_path):
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.append_pending(
		PendingAction(
			id="review-1",
			ts="2026-11-16T00:01:00+00:00",
			platform="zhipin",
			candidate_key="候选人A",
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="executed",
			confidence=0.7,
			reason="needs review",
			message="您好",
			approved_review_id="review-1",
		)
	)

	pending = store.approve_review("review-1", "2026-11-16T00:02:00+00:00")

	# 已存在的行（哪怕已经执行）只补写复核队列，返回它本身。
	assert pending is not None
	assert pending.status == "executed"
	assert len(store.read_pending()) == 1
	assert _review_statuses(store) == [("review-1", "approved")]


def test_reapproving_an_approved_review_returns_none(tmp_path):
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.approve_review("review-1", "2026-11-16T00:01:00+00:00")

	assert store.approve_review("review-1", "2026-11-16T00:02:00+00:00") is None
	assert len(store.read_pending()) == 1
