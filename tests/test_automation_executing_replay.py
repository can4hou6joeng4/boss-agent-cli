"""待执行动作的 in-flight 标记与中断重放回归测试（Issue #1245）。

写动作必须先落盘 `executing` 再派发：请求发出后中断时该行保持 `executing`，
下一轮按「结果未知」转成 `verification-required` 并打开熔断，绝不自动重发。
"""

import pytest

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ActionResult, EventStatus, PendingAction, PlatformAction
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


class _Adapter:
	name = "zhipin"

	def __init__(self, *, interrupt: bool = False, inspect=None) -> None:
		self.sent: list[str] = []
		self.interrupt = interrupt
		self.inspect = inspect

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self) -> str | None:
		return None

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list:
		return []

	def execute_action(self, action, message, ref) -> ActionResult:
		if self.inspect is not None:
			self.inspect()
		self.sent.append(message)
		if self.interrupt:
			raise KeyboardInterrupt()
		return ActionResult(status="executed", details={})


def _seed(
	store: AutomationStore,
	*,
	status: str = "pending",
	platform: str = "zhipin",
	action: str = PlatformAction.SEND_QUESTIONNAIRE.value,
) -> None:
	store.append_pending(
		PendingAction(
			id="approved-1",
			ts="2026-11-16T00:00:00+00:00",
			platform=platform,
			candidate_key="候选人A",
			action=action,
			status=status,
			confidence=0.9,
			reason="approved review",
			message="您好，想确认下近期是否看机会？",
		)
	)


def _statuses(store: AutomationStore) -> list[tuple[str, str]]:
	return [(item.id, item.status) for item in store.read_pending()]


def test_interrupted_action_stays_executing_and_is_never_replayed(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store)
	adapter = _Adapter(interrupt=True)

	with pytest.raises(KeyboardInterrupt):
		run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert _statuses(store) == [("approved-1", "executing")]
	assert len(adapter.sent) == 1

	# 操作员核对前先显式 resume 清熔断；下一轮必须把 executing 按未知结果收口。
	adapter.interrupt = False
	assert store.resume() is True
	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert _statuses(store) == [("approved-1", "verification-required")]
	assert len(adapter.sent) == 1


def test_inflight_marker_is_persisted_before_dispatch(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store)
	seen: list[list[tuple[str, str]]] = []

	adapter = _Adapter(inspect=lambda: seen.append(_statuses(store)))
	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert seen == [[("approved-1", "executing")]]
	assert _statuses(store) == [("approved-1", "executed")]


def test_dry_run_keeps_action_pending_without_inflight_marker(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store)

	run_automation_cycle(_Adapter(), store, AutomationConfig(), platform="zhipin", dry_run=True)

	assert _statuses(store) == [("approved-1", "pending")]


def test_dry_run_with_inflight_row_does_not_open_breaker_or_rewrite_queue(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store, status="executing")

	report = run_automation_cycle(_Adapter(), store, AutomationConfig(), platform="zhipin", dry_run=True)

	# 预览不是执行：遗留的 executing 行保持原样，也不装熔断（Issue #1257）。
	assert _statuses(store) == [("approved-1", "executing")]
	assert not store.stop_path.exists()
	assert report.status == "OK"
	event = report.events[-1]
	assert event.status == "DRY_RUN"
	assert "verification-required" in event.reason


def test_guard_rejection_restores_pending_before_dispatch(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store)
	adapter = _Adapter()

	run_automation_cycle(
		adapter,
		store,
		AutomationConfig(max_actions_per_run=0),
		platform="zhipin",
		dry_run=False,
	)

	assert adapter.sent == []
	assert _statuses(store) == [("approved-1", "pending")]


def test_pending_for_another_platform_is_not_marked_executing(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store, platform="other-platform")

	run_automation_cycle(_Adapter(), store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert _statuses(store) == [("approved-1", "pending")]


def test_local_queue_write_failure_after_success_is_treated_as_unknown(tmp_path, monkeypatch):
	"""平台已成功但写 executed 失败：行停在 executing，下一轮核对而不是重发。"""
	store = AutomationStore(tmp_path)
	_seed(store)
	real_write = store.write_pending
	calls: list[int] = []

	def flaky_write(items):
		calls.append(1)
		if len(calls) == 2:  # 第 1 次是 executing 标记，第 2 次是 executed 回写
			raise OSError("disk full")
		return real_write(items)

	monkeypatch.setattr(store, "write_pending", flaky_write)
	adapter = _Adapter()

	with pytest.raises(OSError):
		run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert len(adapter.sent) == 1
	assert _statuses(store) == [("approved-1", "executing")]

	monkeypatch.setattr(store, "write_pending", real_write)
	store.resume()
	report = run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert report.status == EventStatus.CIRCUIT_BREAKER_OPEN.value
	assert _statuses(store) == [("approved-1", "verification-required")]
	assert len(adapter.sent) == 1


def test_existing_verification_required_row_is_left_untouched(tmp_path):
	store = AutomationStore(tmp_path)
	_seed(store, status="verification-required")
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.sent == []
	assert _statuses(store) == [("approved-1", "verification-required")]


def test_unsent_budget_stop_restores_pending(tmp_path):
	"""预算等待期间收到 stop：request_sent=False 明确未发出，可安全恢复 pending。"""
	store = AutomationStore(tmp_path)
	_seed(store)

	class _BudgetStopAdapter(_Adapter):
		def execute_action(self, action, message, ref):
			raise RequestBudgetExceeded("请求节流等待中收到停止", code="WORKFLOW_STOPPED")

	with pytest.raises(RequestBudgetExceeded):
		run_automation_cycle(_BudgetStopAdapter(), store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert _statuses(store) == [("approved-1", "pending")]


def test_budget_stop_with_sent_flag_keeps_executing(tmp_path):
	"""异常自称可能已发出时不能恢复 pending，否则下一次运行会重发。"""
	store = AutomationStore(tmp_path)
	_seed(store)

	class _SentBudgetStopAdapter(_Adapter):
		def execute_action(self, action, message, ref):
			exc = RequestBudgetExceeded("动作执行后收到停止", code="WORKFLOW_STOPPED")
			exc.details["request_sent"] = True
			raise exc

	with pytest.raises(RequestBudgetExceeded):
		run_automation_cycle(_SentBudgetStopAdapter(), store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert _statuses(store) == [("approved-1", "executing")]
