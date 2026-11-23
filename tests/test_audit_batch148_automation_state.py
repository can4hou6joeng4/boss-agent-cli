"""自动化状态边界：复核关联字段漂移、恢复还原失败、陈旧快照复活闩锁（Issue #2562）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boss_agent_cli.automation.models import PlatformAction, ReviewItem
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings

_TS = "2026-11-20T00:00:00+00:00"
_REVIEWED_AT = "2026-11-20T00:02:00+00:00"


def _pending_row(drift: object) -> dict:
	return {
		"id": "review-1",
		"ts": _TS,
		"platform": "zhipin",
		"candidate_key": "候选人A",
		"action": PlatformAction.SEND_QUESTIONNAIRE.value,
		"status": "pending",
		"confidence": 0.7,
		"reason": "needs review",
		"message": "您好，想确认下近期是否看机会？",
		"approved_review_id": drift,
		"updated_at": _TS,
	}


def _seed_review(store: AutomationStore) -> None:
	store.append_review(
		ReviewItem(
			id="review-1",
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


# ── 1. approved_review_id 类型漂移 fail-closed ─────────────────────


@pytest.mark.parametrize("drift", [None, True, 5, 1.5, ["review-1"], {"id": "review-1"}])
def test_pending_rejects_non_string_approved_review_id(tmp_path: Path, drift: object) -> None:
	store = AutomationStore(tmp_path)
	store.pending_path.write_text(json.dumps(_pending_row(drift), ensure_ascii=False) + "\n", encoding="utf-8")

	with pytest.raises(AutomationStateError, match="approved_review_id"):
		store.read_pending()


def test_pending_accepts_missing_or_string_approved_review_id(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	missing = _pending_row("")
	del missing["approved_review_id"]
	store.pending_path.write_text(
		json.dumps(_pending_row("review-1"), ensure_ascii=False) + "\n"
		+ json.dumps(missing, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)

	rows = store.read_pending()

	assert [row.approved_review_id for row in rows] == ["review-1", ""]


def test_reject_does_not_leave_drifted_pending_row_dispatchable(tmp_path: Path) -> None:
	"""漂移行让 reject 在改写复核之前 fail-closed，不能留下可派发的 pending。"""
	store = AutomationStore(tmp_path)
	_seed_review(store)
	store.pending_path.write_text(
		json.dumps(_pending_row(None), ensure_ascii=False) + "\n", encoding="utf-8"
	)

	with pytest.raises(AutomationStateError, match="approved_review_id"):
		store.reject_review("review-1", "changed mind", _REVIEWED_AT)

	assert [item.status for item in store.read_reviews()] == ["review"]


# ── 2. resume 预检失败：还原失败不能顶替原始异常 ────────────────────


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


class _QuietTransport:
	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		return {"code": 0, "zpData": {"jobList": [], "hasMore": False}}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=2,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=0,
		operating_mode="research",
	)


def test_resume_restore_failure_keeps_original_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = CrawlService(
			cache,
			data_dir=tmp_path,
			transport_factory=lambda settings: _QuietTransport(),
			budget_factory=_NoDelayBudget,
		)
		run_id = service.create(_settings(tmp_path))

		def preflight() -> None:
			raise RuntimeError("preflight boom")

		service = CrawlService(
			cache,
			data_dir=tmp_path,
			transport_factory=lambda settings: _QuietTransport(),
			budget_factory=_NoDelayBudget,
			preflight=preflight,
		)

		def restore_boom(*args, **kwargs):
			raise OSError(28, "no space left on device")

		monkeypatch.setattr(cache, "restore_crawl_run_state", restore_boom)

		with pytest.raises(RuntimeError, match="preflight boom") as caught:
			service.resume(run_id, operating_mode="research")

		notes = getattr(caught.value, "__notes__", [])
		assert any("运行状态还原失败" in note for note in notes)
	finally:
		cache.close()


# ── 3. write_state 不能凭陈旧快照复活已清除的停止闩锁 ────────────────


def _snapshot(reason: str = "old risk") -> dict:
	return {
		"conversations": {},
		"autonomy": {"circuit_breaker": {"open": True, "reason": reason, "opened_at": _TS}},
		"safety": {},
	}


def test_write_state_does_not_revive_cleared_stop_latch(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	# 操作者已通过 resume / 手工清理解除熔断：磁盘既无 stop.json，state 也没有熔断。
	store.write_state({"conversations": {}, "autonomy": {}, "safety": {}})

	store.write_state(_snapshot())

	assert store.read_stop() == {}
	assert store.read_state()["autonomy"]["circuit_breaker"] == {}


def test_write_state_rebuilds_latch_when_disk_still_open(tmp_path: Path) -> None:
	"""磁盘 state 仍处于熔断时保留闩锁，并用磁盘 reason 而不是陈旧快照的。"""
	store = AutomationStore(tmp_path)
	# 直接落盘模拟「磁盘熔断仍成立、但 stop.json 被删」：只有磁盘确认后才能重建。
	store.state_path.write_text(
		json.dumps({
			"conversations": {},
			"autonomy": {"circuit_breaker": {"open": True, "reason": "disk risk", "opened_at": _TS}},
			"safety": {},
		}),
		encoding="utf-8",
	)
	store.stop_path.unlink(missing_ok=True)

	store.write_state(_snapshot("old risk"))

	stop = store.read_stop()
	assert stop.get("open") is True
	assert stop.get("reason") == "disk risk"
