"""队列路径与扫描路径共享会话在途标记（Issue #1514–#1517）。

队列行只保护队列自己：`process_pending` 派发前既不读 `state.json` 的
`inflight_action`，中断后也只把队列行转 `verification-required`，扫描路径
（`process_ref`）看不到。两路径共用同一份标记后：

- 队列派发前命中在途标记：不发送，按标记状态跳过或打开熔断（#1514）；
- 中断的队列行把标记同步进 `state.json`，`--resume` 后扫描路径不会重发（#1515）；
- 成功路径先持久化 state 再推进队列行，checkpoint 失败不会丢 prior（#1516）；
- 「已通知」位只在熔断落盘成功后置位，两步之间失败下一轮会重开熔断（#1517）。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.execution import process_pending
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
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore

CANDIDATE = "候选人A"
INCOMING = "你好，我在上海做过3年销售，擅长客户沟通，大专，期望10-12k，想看机会"


def _conversation(*, outgoing: str = "") -> Conversation:
	ordered = (("outgoing", outgoing), ("incoming", INCOMING)) if outgoing else (("incoming", INCOMING),)
	return Conversation(
		title=CANDIDATE,
		incoming_messages=(INCOMING,),
		outgoing_messages=(outgoing,) if outgoing else (),
		ordered_messages=ordered,
		all_messages=tuple(text for _, text in ordered),
		fingerprint=ConversationFingerprint(CANDIDATE),
	)


class _Adapter(MockRecruiterAutomationPlatform):
	def __init__(
		self,
		*,
		conversation: Conversation | None = None,
		conversations: list[Conversation] | None = None,
		raise_after: BaseException | None = None,
	) -> None:
		if conversations is None:
			conversations = [conversation or _conversation()]
		super().__init__("zhipin", conversations)
		self.sent: list[tuple[PlatformAction, str, str]] = []
		self.raise_after = raise_after

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		self.sent.append((action, message, ref.id))
		if self.raise_after is not None:
			raise self.raise_after
		return ActionResult(status="executed", details={})


def _cycle(adapter: _Adapter, store: AutomationStore, *, dry_run: bool = False, resume: bool = False):
	return run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=dry_run,
		limit=1,
		resume=resume,
	)


def _pending(store: AutomationStore, *, message: str = "您好，想确认下近期是否看机会？") -> None:
	store.append_pending(
		PendingAction(
			id="approved-1",
			ts="2026-11-16T00:00:00+00:00",
			platform="zhipin",
			candidate_key=CANDIDATE,
			action=PlatformAction.SEND_QUESTIONNAIRE.value,
			status="pending",
			confidence=0.9,
			reason="approved review",
			message=message,
		)
	)


def _read_state_file(store: AutomationStore) -> dict[str, Any]:
	path: Path = store.root / "state.json"
	if not path.exists():
		return {}
	return json.loads(path.read_text(encoding="utf-8"))


def _write_state_file(store: AutomationStore, state: dict[str, Any]) -> None:
	(store.root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _inflight(store: AutomationStore, candidate: str = CANDIDATE) -> Any:
	return _read_state_file(store).get("conversations", {}).get(candidate, {}).get("inflight_action")


def _seed_inflight(store: AutomationStore, marker: Any) -> None:
	_write_state_file(
		store,
		{
			"conversations": {CANDIDATE: {"inflight_action": marker}},
			"autonomy": {},
			"safety": {},
		},
	)


def _pending_statuses(store: AutomationStore) -> list[str]:
	return [item.status for item in store.read_pending()]


def test_queue_dispatch_is_blocked_by_scan_inflight_marker(tmp_path: Path) -> None:
	"""扫描路径留下的在途标记同样挡住队列派发（Issue #1514）。"""
	store = AutomationStore(tmp_path)
	_pending(store)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "executing",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
			"message_hash": "sha256:" + "0" * 64,
		},
	)
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert report.events[0].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	marker = _inflight(store)
	assert marker["status"] == "verification-required"
	assert marker["notified"] is True
	assert store.read_stop()["open"] is True
	# 队列行从未派发：保持 pending，人工核对并清理标记后仍可继续。
	assert _pending_statuses(store) == ["pending"]


def test_queue_blocked_by_notified_marker_only_skips_that_row(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_pending(store)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "verification-required",
			"notified": True,
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
		},
	)
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert report.events[0].status == EventStatus.SKIPPED.value
	assert "人工核对" in report.events[0].reason
	assert not store.stop_path.exists()
	assert _pending_statuses(store) == ["pending"]


def test_queue_dispatch_dry_run_does_not_touch_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_pending(store)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "executing",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
		},
	)
	adapter = _Adapter()

	report = _cycle(adapter, store, dry_run=True)

	assert adapter.sent == []
	assert report.events[0].status == EventStatus.DRY_RUN.value
	assert _inflight(store)["status"] == "executing"
	assert not store.stop_path.exists()
	assert _pending_statuses(store) == ["pending"]


def test_interrupted_queue_row_writes_marker_scan_path_cannot_resend(tmp_path: Path) -> None:
	"""队列行发送后中断：标记同步进 state，--resume 后扫描路径只跳过（Issue #1515）。"""
	store = AutomationStore(tmp_path)
	_pending(store, message="您好，想确认下近期是否看机会？")
	adapter = _Adapter(raise_after=SystemExit(1))

	with pytest.raises(SystemExit):
		_cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert _pending_statuses(store) == ["executing"]
	assert _inflight(store)["status"] == "executing"

	recovery_store = AutomationStore(tmp_path)
	recovery = _Adapter()
	report = _cycle(recovery, recovery_store)

	assert recovery.sent == []
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	marker = _inflight(recovery_store)
	assert marker["status"] == "verification-required"
	assert marker["notified"] is True
	assert recovery_store.read_stop()["open"] is True
	assert _pending_statuses(recovery_store) == ["verification-required"]

	# --resume 后扫描路径看到同一份标记：只跳过，不按 transcript 重新决策。
	resumed_store = AutomationStore(tmp_path)
	resumed = _Adapter()
	report = _cycle(resumed, resumed_store, resume=True)

	assert resumed.sent == []
	assert report.events[-1].status == EventStatus.SKIPPED.value
	assert _inflight(resumed_store)["status"] == "verification-required"


def test_state_write_failure_after_send_does_not_resend(tmp_path: Path, monkeypatch) -> None:
	"""平台已发送但 state 未落盘：队列行保持 executing，下一轮不重发（Issue #1516）。"""
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()

	def broken_write_state(state: dict[str, Any]) -> None:
		raise OSError(28, "No space left on device")

	monkeypatch.setattr(store, "write_state", broken_write_state)
	with pytest.raises(OSError):
		_cycle(adapter, store)
	assert len(adapter.sent) == 1
	# 派发前与发送后的两次状态写入都失败：队列行留在 executing 作为重放屏障。
	assert _pending_statuses(store) == ["executing"]

	monkeypatch.undo()
	replay_store = AutomationStore(tmp_path)
	replay = _Adapter()
	report = _cycle(replay, replay_store)

	assert replay.sent == []
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert _pending_statuses(replay_store) == ["verification-required"]
	assert _inflight(replay_store)["status"] == "verification-required"


def test_breaker_failure_before_notified_is_reopened_next_cycle(tmp_path: Path, monkeypatch) -> None:
	"""标记落盘、熔断未落盘：下一轮重新打开熔断，不会静默跳过（Issue #1517）。"""
	store = AutomationStore(tmp_path)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "executing",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
		},
	)
	original = SafetyGuard.open_circuit_breaker
	calls = {"count": 0}

	def flaky_open(self: SafetyGuard, reason: str) -> None:
		calls["count"] += 1
		if calls["count"] == 1:
			raise SystemExit(1)
		original(self, reason)

	monkeypatch.setattr(SafetyGuard, "open_circuit_breaker", flaky_open)
	adapter = _Adapter()
	with pytest.raises(SystemExit):
		_cycle(adapter, store)

	assert adapter.sent == []
	marker = _inflight(store)
	assert marker["status"] == "verification-required"
	assert marker["notified"] is False
	assert not store.stop_path.exists()

	monkeypatch.undo()
	replay_store = AutomationStore(tmp_path)
	replay = _Adapter()
	report = _cycle(replay, replay_store)

	assert replay.sent == []
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert replay_store.read_stop()["open"] is True
	assert _inflight(replay_store)["notified"] is True


def test_successful_queue_dispatch_settles_marker_and_prior(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter(conversations=[])
	state = store.read_state()
	guard = SafetyGuard(AutomationConfig(), state, dry_run=False, store=store)

	events = process_pending(adapter, store, guard, "zhipin", False, state=state)

	assert len(adapter.sent) == 1
	assert events[0].status == EventStatus.AUTO_EXECUTED.value
	prior = _read_state_file(store)["conversations"][CANDIDATE]
	assert "inflight_action" not in prior
	assert prior["questionnaire_sent_at"]
	assert _pending_statuses(store) == ["executed"]
	assert not store.stop_path.exists()


def test_process_pending_accepts_missing_state(tmp_path: Path) -> None:
	"""没有 state 的调用方（测试 / 旧入口）保持原行为，不因新增标记崩溃。"""
	store = AutomationStore(tmp_path)
	_pending(store)
	adapter = _Adapter()
	guard = SafetyGuard(AutomationConfig(), {}, dry_run=False)

	events = process_pending(adapter, store, guard, "zhipin", False)

	assert len(adapter.sent) == 1
	assert events[0].status == EventStatus.AUTO_EXECUTED.value
	assert _pending_statuses(store) == ["executed"]
