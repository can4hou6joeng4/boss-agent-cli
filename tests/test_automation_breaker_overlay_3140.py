"""关闭的 stop.json 不能解除 state.json 里仍打开的熔断（Issue #3140）。"""

import json

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import CandidateKey, Decision, PlatformAction
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore


def _decision() -> Decision:
	return Decision(
		action=PlatformAction.SEND_QUESTIONNAIRE,
		confidence=0.9,
		reason="规则命中",
		candidate_key=CandidateKey("synthetic"),
		message="模板话术",
	)


def test_closed_stop_marker_does_not_clear_open_snapshot(tmp_path):
	store = AutomationStore(tmp_path)
	(store.root / "stop.json").write_text(
		json.dumps({"open": False, "reason": "explicit-resume-after-verification"}), encoding="utf-8"
	)
	(store.root / "state.json").write_text(
		json.dumps({
			"autonomy": {"circuit_breaker": {"open": True, "reason": "manual-stop"}},
			"safety": {},
			"conversations": {},
		}),
		encoding="utf-8",
	)

	assert AutomationStore.stop_reason_for(tmp_path) == "manual-stop"
	assert store.read_state()["autonomy"]["circuit_breaker"]["open"] is True

	guard = SafetyGuard(AutomationConfig(), {}, dry_run=False, store=store)
	assert guard.stop_reason() == "manual-stop"
	assert guard.before_action(_decision()).allowed is False

	assert store.resume() is True
	assert AutomationStore.stop_reason_for(tmp_path) is None
