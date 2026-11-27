"""中断清理已收到风控终止结果时不得改成可重试（Issue #3000）。"""

import asyncio
import json
from types import SimpleNamespace

import anyio
import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.display import RISK_ERROR_CONTRACTS, risk_error_contract
from boss_agent_cli.output import envelope_error


def _risk_payload(code: str) -> dict:
	recovery, hints = risk_error_contract(code)
	return json.loads(envelope_error(
		"wizard", code=code, message="离线风控夹具", recoverable=False,
		recovery_action=recovery, hints=hints,
		details={"run_id": "fixture-run", "replay_safe": False},
	))


@pytest.mark.parametrize("risk_code", RISK_ERROR_CONTRACTS)
@pytest.mark.parametrize("interrupt_code", ["WORKFLOW_TIMEOUT", "WORKFLOW_STOPPED"])
@pytest.mark.parametrize("may_write", [False, True])
def test_interrupted_cleanup_preserves_complete_risk_envelope(risk_code, interrupt_code, may_write):
	child = _risk_payload(risk_code)
	result = mcp_server._interrupted_failure(
		("wizard", "--resume", "fixture-run"), interrupt_code,
		json.dumps(child).encode(), may_write=may_write,
	)
	# 风控码、人工恢复动作、断点与 hints 一并保留，不再建议重发。
	assert result == child
	assert result["error"]["recoverable"] is False
	assert result["error"]["details"]["replay_safe"] is False


@pytest.mark.parametrize("stdout", [
	b"", b"not json", b'{"error":{"code":"ACCOUNT_RISK"}}',
	b'{"ok":false,"schema_version":"1.0","command":"chatmsg","data":null,'
	b'"pagination":null,"error":{"code":"ACCOUNT_RISK","recoverable":"false",'
	b'"recovery_action":"stop"},"hints":null}',
])
def test_incomplete_risk_output_keeps_existing_interrupt_contract(stdout):
	result = mcp_server._interrupted_failure(("chatmsg", "fixture-uid"), "WORKFLOW_TIMEOUT", stdout, may_write=False)
	assert result["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert result["error"]["details"]["outcome_unknown"] is False


@pytest.mark.parametrize("phase", ["spawning", "running", "timeout"])
def test_risk_result_survives_actual_async_cleanup_paths(monkeypatch, phase):
	child = _risk_payload("ENVIRONMENT_RISK")

	async def exercise():
		stopped = asyncio.Event()
		signals = []

		async def communicate():
			if phase == "running":
				scope.cancel()
			await stopped.wait()
			return json.dumps(child).encode(), b""

		def send_signal(sig):
			signals.append(sig)
			process.returncode = 1
			stopped.set()

		process = SimpleNamespace(returncode=None, communicate=communicate, send_signal=send_signal)

		async def spawn(*args, **kwargs):
			if phase == "spawning":
				scope.cancel()
			await asyncio.sleep(0)
			return process

		monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", spawn)
		monkeypatch.setattr(mcp_server, "_cli_timeout", lambda args: 0.01)
		result = None
		with anyio.CancelScope() as scope:
			result = await mcp_server._run_boss_async("chatmsg", "fixture-uid", may_write=False)
		assert len(signals) == 1
		assert result == child

	asyncio.run(exercise())
