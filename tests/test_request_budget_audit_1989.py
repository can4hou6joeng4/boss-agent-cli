"""请求预算租约诊断字段回归（Issue #1989–#1990）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import RequestBudget, RequestBudgetExceeded
from boss_agent_cli.services.file_lock import try_lock_exclusive


def test_lease_does_not_overwrite_existing_risk_lock(tmp_path: Path) -> None:
	"""租约异常路径不再用缺失的平台码重写已落盘的锁（#1989）。"""
	budget = RequestBudget(tmp_path, interval=0, max_per_minute=1000, wait_timeout=5)

	with pytest.raises(PlatformRiskError) as exc_info:
		with budget._lease(time.monotonic(), None):
			# 模拟另一个写锁方（如登录流程）在请求期间的某次体内检查前落锁。
			cdp_risk_lock.write_lock(tmp_path, None, reason="ACCOUNT_RISK", platform_code=32)
			budget.check_risk()

	assert exc_info.value.details["risk_lock"]["platform_code"] == 32
	lock = cdp_risk_lock.read_lock(tmp_path)
	assert lock is not None
	assert lock.platform_code == 32, "已落盘锁的诊断字段不能被重写成 null"


def test_lease_exception_without_persisted_lock_still_records(tmp_path: Path) -> None:
	"""没有 risk_lock 详情的风险错误仍由租约异常路径兜底落锁（#1989 护栏）。"""

	class _BareRiskError(PlatformRiskError):
		code = "ACCOUNT_RISK"

	error = _BareRiskError("平台账号风险", details={"platform_code": 36})
	budget = RequestBudget(tmp_path, interval=0, max_per_minute=1000, wait_timeout=5)

	with pytest.raises(_BareRiskError):
		with budget._lease(time.monotonic(), None):
			raise error

	lock = cdp_risk_lock.read_lock(tmp_path)
	assert lock is not None
	assert lock.reason == "ACCOUNT_RISK"
	assert lock.platform_code == 36


def test_lease_contention_exhaustion_has_no_misleading_retry_after(tmp_path: Path) -> None:
	"""租约竞争耗尽等待窗口时不报最后一个 50ms 轮询片（#1990）。"""
	budget = RequestBudget(tmp_path, interval=0, max_per_minute=1000, wait_timeout=0.2)
	lease_path = budget.root / "request-budget.lease"
	lease_path.write_bytes(b"\0")
	holder = lease_path.open("a+b")
	try:
		assert try_lock_exclusive(holder)
		with pytest.raises(RequestBudgetExceeded) as exc_info:
			with budget._lease(time.monotonic(), None):
				pass
	finally:
		holder.close()

	assert exc_info.value.code == "REQUEST_BUDGET_EXHAUSTED"
	assert exc_info.value.details["request_sent"] is False
	assert "retry_after_seconds" not in exc_info.value.details


def test_budget_wait_still_reports_real_retry_after(tmp_path: Path) -> None:
	"""冷却 / 窗口等待耗尽窗口时仍报真实等待值（#1990 护栏）。"""
	budget = RequestBudget(tmp_path, interval=0, max_per_minute=1000, wait_timeout=0.2)
	started = time.monotonic() - 1.0

	with pytest.raises(RequestBudgetExceeded) as exc_info:
		budget._pause(5.0, started, None)

	assert exc_info.value.details["retry_after_seconds"] == pytest.approx(5.0)
