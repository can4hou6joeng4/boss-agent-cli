"""直接扫描路径的在途动作落盘与重放（Issue #1511）。

`process_ref` 在派发前把可能发出的动作写进 `state.json` 的会话记录；发送成功
后 settle，结果未知时保留标记并在下一轮转 `verification-required`，绝不凭
transcript 重新决策后重发。旧 state 缺少该字段照常运行，类型漂移按「存在未
确认动作」fail-closed。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.api.browser_client import ChatTabCdpUnreachable, RecruiterChatTabRequired
from boss_agent_cli.api.browser_source import POLICIES, BrowserSourceUnavailable
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.mock_adapter import MockRecruiterAutomationPlatform
from boss_agent_cli.automation.models import (
	ActionResult,
	Conversation,
	ConversationFingerprint,
	ConversationRef,
	EventStatus,
	PlatformAction,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import (
	AutomationControlBusy,
	AutomationControlBusyDispatched,
	AutomationStore,
)

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
		before_execute=None,
		after_execute=None,
		raise_before: BaseException | None = None,
		raise_after: BaseException | None = None,
		result: ActionResult | None = None,
	) -> None:
		super().__init__("zhipin", [conversation or _conversation()])
		self.sent: list[tuple[PlatformAction, str, str]] = []
		self.before_execute = before_execute
		self.after_execute = after_execute
		self.raise_before = raise_before
		self.raise_after = raise_after
		self.result = result or ActionResult(status="executed", details={})

	def execute_action(
		self,
		action: PlatformAction,
		message: str,
		ref: ConversationRef,
	) -> ActionResult:
		if self.raise_before is not None:
			raise self.raise_before
		if self.before_execute is not None:
			self.before_execute()
		self.sent.append((action, message, ref.id))
		if self.after_execute is not None:
			self.after_execute()
		if self.raise_after is not None:
			raise self.raise_after
		return self.result


def _cycle(adapter: _Adapter, store: AutomationStore, *, dry_run: bool = False):
	return run_automation_cycle(
		adapter,
		store,
		AutomationConfig(),
		platform="zhipin",
		dry_run=dry_run,
		limit=1,
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


def test_inflight_marker_persisted_before_dispatch_and_settled_after_success(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	seen: list[Any] = []
	adapter = _Adapter(before_execute=lambda: seen.append(_inflight(store)))

	report = _cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert adapter.sent[0][0] is PlatformAction.SEND_QUESTIONNAIRE
	assert seen[0]["action"] == PlatformAction.SEND_QUESTIONNAIRE.value
	assert seen[0]["status"] == "executing"
	assert seen[0]["conversation_ref"] == CANDIDATE
	assert seen[0]["message_hash"].startswith("sha256:")
	assert seen[0]["started_at"]

	prior = _read_state_file(store)["conversations"][CANDIDATE]
	assert "inflight_action" not in prior
	assert prior["questionnaire_sent_at"]
	assert not store.stop_path.exists()
	assert report.status == "OK"
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value


def test_process_killed_after_send_replay_is_verification_required(tmp_path: Path) -> None:
	"""发送后进程退出（无 finally 写回）：磁盘只剩派发前的在途标记。"""
	store = AutomationStore(tmp_path)
	adapter = _Adapter(raise_after=SystemExit(1))

	with pytest.raises(SystemExit):
		_cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert _inflight(store)["status"] == "executing"
	assert not store.stop_path.exists()

	# 新的 state / 新的周期：不发送问卷、不凭 transcript 重新决策。
	replay_store = AutomationStore(tmp_path)
	replay = _Adapter()
	report = _cycle(replay, replay_store)

	assert replay.sent == []
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	event = report.events[-1]
	assert event.status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert "核对" in event.reason
	assert _inflight(replay_store)["status"] == "verification-required"
	assert replay_store.read_stop()["open"] is True


@pytest.mark.parametrize(
	"action",
	[
		pytest.param(PlatformAction.SEND_QUESTIONNAIRE, id="questionnaire"),
		pytest.param(PlatformAction.SEND_FOLLOW_UP, id="follow-up"),
	],
)
def test_replay_of_each_message_action_is_verification_required(
	tmp_path: Path, action: PlatformAction
) -> None:
	store = AutomationStore(tmp_path)
	_seed_inflight(
		store,
		{
			"action": action.value,
			"status": "executing",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
		},
	)
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	event = report.events[-1]
	assert event.status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert event.action == action.value
	assert _inflight(store)["status"] == "verification-required"


def test_verification_required_marker_is_skipped_after_resume(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "verification-required",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
			"message_hash": "sha256:" + "0" * 64,
		},
	)
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert report.events[-1].status == EventStatus.SKIPPED.value
	assert "人工核对" in report.events[-1].reason
	# 操作者已收到过核对要求：不再重复打开熔断。
	assert not store.stop_path.exists()


def test_legacy_state_without_inflight_field_runs_normally(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	_write_state_file(store, {"conversations": {}, "autonomy": {}, "safety": {}})
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value
	assert "inflight_action" not in _read_state_file(store)["conversations"][CANDIDATE]


@pytest.mark.parametrize(
	"marker",
	[
		pytest.param("sent", id="marker-not-object"),
		pytest.param(["send_questionnaire"], id="marker-list"),
		pytest.param({"action": 123, "status": "executing"}, id="action-not-string"),
		pytest.param({"action": None, "status": "executing"}, id="action-null"),
		pytest.param({"action": "not-an-action", "status": "executing"}, id="action-unknown"),
		pytest.param({"action": "send_questionnaire", "status": 5}, id="status-not-string"),
		pytest.param({"action": "send_questionnaire", "status": "settled"}, id="status-unknown"),
		pytest.param({}, id="marker-empty-object"),
	],
)
def test_type_drifted_inflight_marker_fails_closed(tmp_path: Path, marker: Any) -> None:
	store = AutomationStore(tmp_path)
	_seed_inflight(store, marker)
	adapter = _Adapter()

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert report.events[-1].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert _inflight(store)["status"] == "verification-required"
	assert store.read_stop()["open"] is True


def test_ai_rewritten_transcript_does_not_bypass_inflight_marker(tmp_path: Path) -> None:
	"""本地 AI 改写后的文案匹配不到模板，仍要按在途标记停下而不是重发。"""
	store = AutomationStore(tmp_path)
	_seed_inflight(
		store,
		{
			"action": PlatformAction.SEND_QUESTIONNAIRE.value,
			"status": "executing",
			"started_at": "2026-11-16T00:00:00+00:00",
			"conversation_ref": CANDIDATE,
			"message_hash": "sha256:" + "1" * 64,
		},
	)
	adapter = _Adapter(
		conversation=_conversation(outgoing="您好，我是招聘方，方便聊聊您近期的机会吗？")
	)

	report = _cycle(adapter, store)

	assert adapter.sent == []
	assert report.events[-1].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value


def test_dry_run_does_not_write_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	adapter = _Adapter()

	report = _cycle(adapter, store, dry_run=True)

	assert adapter.sent == []
	assert _inflight(store) is None
	assert not store.stop_path.exists()
	assert report.events[0].status == EventStatus.DRY_RUN.value


def test_dry_run_with_inflight_marker_keeps_it_and_opens_no_breaker(tmp_path: Path) -> None:
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
	adapter = _Adapter()

	report = _cycle(adapter, store, dry_run=True)

	assert adapter.sent == []
	assert _inflight(store)["status"] == "executing"
	assert not store.stop_path.exists()
	assert report.events[-1].status == EventStatus.DRY_RUN.value
	assert "verification-required" in report.events[-1].reason


@pytest.mark.parametrize(
	"error",
	[
		pytest.param(ChatTabCdpUnreachable("cdp unreachable"), id="chat-tab-cdp-unreachable"),
		pytest.param(RecruiterChatTabRequired(), id="recruiter-chat-tab-required"),
		pytest.param(
			BrowserSourceUnavailable(POLICIES["existing-browser"], ("cdp",)),
			id="browser-source-unavailable",
		),
	],
)
def test_predispatch_error_releases_inflight_marker(tmp_path: Path, error: BaseException) -> None:
	store = AutomationStore(tmp_path)
	adapter = _Adapter(raise_before=error)

	with pytest.raises(type(error)):
		_cycle(adapter, store)

	assert adapter.sent == []
	assert _inflight(store) is None
	assert not store.stop_path.exists()

	# 标记已释放：下一轮可以正常评估并派发。
	replay = _Adapter()
	report = _cycle(replay, store)
	assert len(replay.sent) == 1
	assert report.events[0].status == EventStatus.AUTO_EXECUTED.value


def test_budget_stop_before_send_releases_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	error = RequestBudgetExceeded("request budget exhausted", code="WORKFLOW_STOPPED")
	adapter = _Adapter(raise_before=error)

	with pytest.raises(RequestBudgetExceeded):
		_cycle(adapter, store)

	assert adapter.sent == []
	assert _inflight(store) is None


def test_budget_stop_after_send_keeps_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	error = RequestBudgetExceeded("stopped after dispatch", code="WORKFLOW_STOPPED")
	error.details["request_sent"] = True
	adapter = _Adapter(raise_after=error)

	with pytest.raises(RequestBudgetExceeded):
		_cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert _inflight(store)["status"] == "executing"

	store.resume()
	replay = _Adapter()
	report = _cycle(replay, store)
	assert replay.sent == []
	assert report.events[-1].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value


def test_unknown_error_after_send_keeps_inflight_marker(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	adapter = _Adapter(raise_after=TimeoutError("timed out"))

	with pytest.raises(TimeoutError):
		_cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert _inflight(store)["status"] == "executing"

	replay = _Adapter()
	report = _cycle(replay, store)
	assert replay.sent == []
	assert report.events[-1].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value


def test_checkpoint_control_lock_after_send_keeps_marker_and_reports_dispatched(
	tmp_path: Path, monkeypatch
) -> None:
	"""发送后 checkpoint 撞控制锁：磁盘保留在途标记，周期按「可能已发出」上报。"""
	store = AutomationStore(tmp_path)
	real_write = store.write_state
	calls: list[int] = []

	def flaky_write(state: dict[str, Any]) -> None:
		calls.append(1)
		# 第 1 次是派发前的在途标记，第 2 次是发送成功后的 checkpoint。
		if len(calls) == 2:
			raise AutomationControlBusy("busy")
		real_write(state)

	monkeypatch.setattr(store, "write_state", flaky_write)
	adapter = _Adapter()

	with pytest.raises(AutomationControlBusyDispatched):
		_cycle(adapter, store)

	assert len(adapter.sent) == 1
	assert _inflight(store)["status"] == "executing"

	monkeypatch.setattr(store, "write_state", real_write)
	replay = _Adapter()
	report = _cycle(replay, store)

	assert replay.sent == []
	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert report.events[-1].status == EventStatus.PLATFORM_VERIFICATION_REQUIRED.value
	assert _inflight(store)["status"] == "verification-required"
