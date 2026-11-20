"""批次七十三回归测试：crawl 清理竞态、resume 停止窗口、详情缓存漂移与自动化重复派发（Issue #2208–#2211）。

全部离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
	PendingAction,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlStopRequested
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _settings(tmp_path: Path, *, pages: int = 2, with_detail: bool = False) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=pages,
		with_detail=with_detail,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=0,
		operating_mode="research",
	)


def _service(
	cache: CacheStore,
	tmp_path: Path,
	transport_factory,
	*,
	preflight=None,
) -> CrawlService:
	return CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory,
		budget_factory=_NoDelayBudget,
		preflight=preflight,
	)


# ── Issue #2208：断点被 clean --privacy 删除后 fail-closed ───────────────


class _DeletingTransport:
	"""第一页返回前删除断点行，模拟并发执行 clean --privacy。"""

	def __init__(self, db_path: Path) -> None:
		self._db_path = db_path
		self.page_calls: list[int] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		with sqlite3.connect(self._db_path) as conn:
			conn.execute("DELETE FROM crawl_jobs")
			conn.execute("DELETE FROM crawl_runs")
		return {
			"code": 0,
			"zpData": {
				"jobList": [{"encryptJobId": "job-1", "jobName": "测试职位", "brandName": "测试公司"}],
				"hasMore": True,
			},
		}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def test_resume_stops_when_run_deleted_mid_crawl(tmp_path: Path) -> None:
	"""断点被清理后不再写入职位行、不再翻页、不再重建导出（Issue #2208）。"""
	cache = CacheStore(tmp_path / "cache.db")
	try:
		transport = _DeletingTransport(tmp_path / "cache.db")
		output_dir = tmp_path / "out"
		service = _service(cache, tmp_path, lambda settings: transport)
		run_id = service.create(_settings(tmp_path, pages=3))

		outcome = service.resume(run_id, operating_mode="research")

		assert outcome.status == "stopped"
		assert transport.page_calls == [1], "断点消失后不能再发平台请求"
		assert cache.list_crawl_jobs(run_id) == [], "已清理的数据不能被复活"
		assert cache.get_crawl_run(run_id) is None
		assert not output_dir.exists(), "不能重建已删除的导出目录"
	finally:
		cache.close()


def test_stop_check_fails_closed_when_run_row_missing(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = _service(cache, tmp_path, lambda settings: None)
		with pytest.raises(CrawlStopRequested):
			service._raise_if_stop_requested("missing-run")
	finally:
		cache.close()


def test_put_crawl_job_refuses_orphan_rows(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		assert (
			cache.put_crawl_job(
				"missing-run", "job-1", 1, {"job_id": "job-1"}, detail_done=False, require_existing_run=True
			)
			is False
		)
		# 默认写入仍允许先构造职位行（离线工具与测试夹具依赖这一行为）。
		assert cache.put_crawl_job("missing-run", "job-2", 1, {"job_id": "job-2"}, detail_done=False) is True
		assert [item["job_key"] for item in cache.list_crawl_jobs("missing-run")] == ["job-2"]
	finally:
		cache.close()


# ── Issue #2209：前台 resume 预检窗口内的新 stop 必须保留 ────────────────


class _CountingTransport:
	def __init__(self) -> None:
		self.page_calls: list[int] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		return {"code": 0, "zpData": {"jobList": [], "hasMore": False}}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def test_foreground_resume_keeps_stop_arriving_during_preflight(tmp_path: Path) -> None:
	"""预检期间到达的新 stop 不能被预检后的清理一起清掉（Issue #2209）。"""
	cache = CacheStore(tmp_path / "cache.db")
	try:
		transport = _CountingTransport()
		service = _service(cache, tmp_path, lambda settings: transport)
		run_id = service.create(_settings(tmp_path, pages=2))
		# 上一轮崩溃留下的停止请求：resume 的 clear_stop 会清它，但预检期间
		# 到达的新 stop 必须保留。
		cache.request_crawl_stop(run_id)

		def preflight() -> None:
			cache.request_crawl_stop(run_id)

		service = _service(cache, tmp_path, lambda settings: transport, preflight=preflight)

		outcome = service.resume(run_id, operating_mode="research", clear_stop=True)

		assert outcome.status == "stopped"
		assert transport.page_calls == [], "预检期间到达的 stop 必须立即生效"
	finally:
		cache.close()


def test_foreground_resume_restores_stop_when_preflight_fails(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		service = _service(cache, tmp_path, lambda settings: _CountingTransport())
		run_id = service.create(_settings(tmp_path, pages=2))
		cache.request_crawl_stop(run_id)

		def preflight() -> None:
			raise RuntimeError("preflight boom")

		service = _service(cache, tmp_path, lambda settings: _CountingTransport(), preflight=preflight)

		with pytest.raises(RuntimeError):
			service.resume(run_id, operating_mode="research", clear_stop=True)

		run = cache.get_crawl_run(run_id)
		assert run is not None and run["stop_requested"] is True
	finally:
		cache.close()


# ── Issue #2210：job_desc_cache 的 BLOB 漂移按可重建缓存处理 ─────────────


def test_get_job_desc_decodes_blob_value(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		cache.put_job_desc("job-1", "缓存描述")
		cache._conn.execute(
			"UPDATE job_desc_cache SET description = CAST('缓存描述' AS BLOB) WHERE job_id = ?", ("job-1",)
		)
		cache._conn.commit()

		assert cache.get_job_desc("job-1") == "缓存描述"
	finally:
		cache.close()


def test_get_job_desc_treats_undecodable_blob_as_miss(tmp_path: Path) -> None:
	cache = CacheStore(tmp_path / "cache.db")
	try:
		cache.put_job_desc("job-1", "x")
		cache._conn.execute(
			"UPDATE job_desc_cache SET description = ? WHERE job_id = ?", (sqlite3.Binary(b"\xff\xfe"), "job-1")
		)
		cache._conn.commit()

		assert cache.get_job_desc("job-1") is None
	finally:
		cache.close()


class _NoDetailTransport:
	def __init__(self) -> None:
		self.detail_calls: list[str] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		return {
			"code": 0,
			"zpData": {
				"jobList": [
					{"encryptJobId": "job-1", "securityId": "sec-1", "jobName": "测试职位", "brandName": "测试公司"}
				],
				"hasMore": False,
			},
		}

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		raise AssertionError("缓存命中时不应请求详情")

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def test_crawl_uses_blob_detail_cache_without_crashing(tmp_path: Path) -> None:
	"""BLOB 详情缓存命中后 payload 仍是字符串，任务不会永久停在 stopped（Issue #2210）。"""
	cache = CacheStore(tmp_path / "cache.db")
	try:
		transport = _NoDetailTransport()
		service = _service(cache, tmp_path, lambda settings: transport)
		run_id = service.create(_settings(tmp_path, pages=1, with_detail=True))
		# 先跑一次让职位行落库，再把详情缓存改成 BLOB 并重置待补状态。
		first = service.resume(run_id, operating_mode="research")
		assert first.status == "completed"
		cache.put_job_desc("job-1", "缓存描述")
		cache._conn.execute(
			"UPDATE job_desc_cache SET description = CAST('缓存描述' AS BLOB) WHERE job_id = ?", ("job-1",)
		)
		job = cache.list_crawl_jobs(run_id)[0]
		cache.put_crawl_job(run_id, job["job_key"], job["page_no"], dict(job["payload"]), detail_done=False)
		cache._conn.commit()
		transport.detail_calls.clear()

		second = service.resume(run_id, operating_mode="research", with_detail=True)

		assert second.status == "completed", second.error
		assert transport.detail_calls == []
		payload = cache.list_crawl_jobs(run_id)[0]["payload"]
		assert payload["post_description"] == "缓存描述"
		assert isinstance(payload["post_description"], str)
	finally:
		cache.close()


# ── Issue #2211：队列认领的会话不再交给扫描路径，重复动作不再发送 ────────

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"
APPROVED_MESSAGE = "APPROVED-MESSAGE"


def _conversation() -> Conversation:
	return Conversation(
		title=CANDIDATE,
		incoming_messages=(INCOMING,),
		outgoing_messages=(),
		ordered_messages=(("incoming", INCOMING),),
		all_messages=(INCOMING,),
		fingerprint=ConversationFingerprint(CANDIDATE),
	)


class _BlockedOnceAdapter(MockRecruiterAutomationPlatform):
	def __init__(self, conversations: list[Conversation] | None = None, *, scan_empty: bool = False) -> None:
		super().__init__("zhipin", conversations if conversations is not None else [_conversation()])
		self.sent: list[tuple[PlatformAction, str, str]] = []
		self.blocked_once = False
		self.scan_empty = scan_empty

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list[ConversationRef]:
		if self.scan_empty:
			return []
		return super().scan_conversations(tabs, max_per_tab)

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.sent.append((action, message, ref.id))
		if not self.blocked_once:
			self.blocked_once = True
			return ActionResult(status="blocked", details={"reason": "rejected"})
		return ActionResult(status="executed", details={})


def _seed_pending(store: AutomationStore, *, action: PlatformAction = PlatformAction.SEND_QUESTIONNAIRE) -> None:
	store.append_pending(
		PendingAction(
			id="approved-1",
			ts="2026-11-20T00:00:00+00:00",
			platform="zhipin",
			candidate_key=CANDIDATE,
			action=action.value,
			status="pending",
			confidence=0.9,
			reason="approved review",
			message=APPROVED_MESSAGE,
		)
	)


def _state_path(store: AutomationStore) -> Path:
	return store.root / "state.json"


def _seed_prior(store: AutomationStore, key: str) -> None:
	_state_path(store).write_text(
		json.dumps(
			{"conversations": {CANDIDATE: {key: "2026-11-20T00:00:00+00:00"}}, "autonomy": {}, "safety": {}},
			ensure_ascii=False,
		),
		encoding="utf-8",
	)


def test_queue_blocked_does_not_let_scan_path_send_in_same_cycle(tmp_path: Path) -> None:
	"""队列派发失败后，同一轮扫描路径不能顶替发送模板话术（Issue #2211）。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	adapter = _BlockedOnceAdapter()

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
	)

	assert adapter.sent == [(PlatformAction.SEND_QUESTIONNAIRE, APPROVED_MESSAGE, CANDIDATE)]
	assert [item.status for item in store.read_pending()] == ["pending"]
	assert report.events, "队列派发应产生事件"


def test_stale_queue_row_not_resent_when_stage_already_done(tmp_path: Path) -> None:
	"""扫描路径已完成同一动作时，遗留 pending 行标记为已执行而不是再发（Issue #2211）。"""
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_prior(store, "questionnaire_sent_at")
	adapter = _BlockedOnceAdapter(scan_empty=True)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=False,
	)

	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["executed"]
	assert report.events[0].status == EventStatus.SKIPPED.value
	assert "已执行" in report.events[0].reason


def test_stale_queue_row_dry_run_does_not_mutate(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_pending(store)
	_seed_prior(store, "questionnaire_sent_at")
	adapter = _BlockedOnceAdapter(scan_empty=True)

	report = run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=True,
	)

	assert adapter.sent == []
	assert [item.status for item in store.read_pending()] == ["pending"]
	assert report.events[0].status == EventStatus.DRY_RUN.value
