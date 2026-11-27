"""闩锁已生效时不能因 state.json 快照写入失败误报未持久化（Issue #3077）。

``automation/state.json`` 是指向普通文件的符号链接时，读取侧允许（Issue
#1728）、写入侧拒绝（Issue #1746）。风控周期里 ``open_circuit_breaker`` 先写
``stop.json``（权威闩锁）成功，再 checkpoint ``state.json`` 失败：闩锁事实上
已经打开，``risk_latch_persisted`` 必须反映 ``stop.json`` 的真实状态。
"""

import json
import os
from pathlib import Path

import pytest

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore


class _RiskAdapter:
	def ensure_session(self) -> None:
		raise AccountRiskError("平台要求完成安全验证")

	def detect_safety_warning(self) -> None:
		return None


def test_symlinked_state_latch_reports_persisted(tmp_path: Path) -> None:
	target = tmp_path / "state-target.json"
	target.write_text(json.dumps({"conversations": {}, "autonomy": {}, "safety": {}}), encoding="utf-8")
	(tmp_path / "automation").mkdir()
	link = tmp_path / "automation" / "state.json"
	os.symlink(target, link)

	store = AutomationStore(tmp_path)
	with pytest.raises(AccountRiskError) as excinfo:
		run_automation_cycle(_RiskAdapter(), store, AutomationConfig(), platform="zhipin", dry_run=False)

	# stop.json 是权威闩锁：checkpoint state.json 失败不影响「闩锁已持久化」。
	assert store.read_stop()["open"] is True
	assert AutomationStore.stop_reason_for(tmp_path) == "ACCOUNT_RISK"
	details = excinfo.value.details
	assert details["risk_latch_persisted"] is True
	# 诊断保留：state.json 快照没能写回的原始原因。
	assert "AutomationStateError" in details["risk_latch_error"]
	assert link.is_symlink()
