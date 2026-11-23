"""MCP 请求取消时，宿主仍须停止并回收自己启动的 CLI。"""

import asyncio
import signal
from types import SimpleNamespace

import anyio
import pytest

from boss_agent_cli import mcp_server


@pytest.mark.parametrize("phase", ["spawning", "running"])
def test_anyio_cancellation_keeps_child_cleanup_owned(monkeypatch, phase):
	async def exercise():
		stopped = asyncio.Event()
		signals = []

		async def communicate():
			if phase == "running":
				scope.cancel()
			await stopped.wait()
			return b'{"data":{"run_id":"wrn_demo"}}', b""

		def send_signal(sig):
			signals.append(sig)
			process.returncode = -sig
			stopped.set()

		process = SimpleNamespace(returncode=None, communicate=communicate, send_signal=send_signal)

		async def fake_spawn(*args, **kwargs):
			if phase == "spawning":
				# 启动任务在宿主收到取消时尚未取得进程句柄。
				scope.cancel()
				ready = asyncio.Event()
				asyncio.get_running_loop().call_later(0.01, ready.set)
				await ready.wait()
			return process

		monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fake_spawn)
		result = None
		with anyio.CancelScope() as scope:
			result = await mcp_server._run_boss_async("wizard", "--resume", "wrn_demo")

		assert signals == [signal.SIGINT]
		assert process.returncode == -signal.SIGINT
		assert result is not None
		assert result["error"]["code"] == "WORKFLOW_STOPPED"
		details = result["error"]["details"]
		assert details["replay_safe"] is False
		assert details["checkpoint_result"]["data"]["run_id"] == "wrn_demo"

	asyncio.run(exercise())


def test_estimation_failure_never_starts_cli(monkeypatch):
	def fail_estimation(args):
		raise OverflowError("模拟本地估算越界")

	async def unexpected_spawn(*args, **kwargs):
		pytest.fail("本地估算失败后不应启动子进程")

	monkeypatch.setattr(mcp_server, "_cli_timeout", fail_estimation)
	monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", unexpected_spawn)
	result = asyncio.run(mcp_server._run_boss_async("wizard", "--resume", "wrn_demo"))
	assert result["error"]["code"] == "INVALID_PARAM"
	assert result["error"]["details"]["outcome_unknown"] is False
	assert "未启动" in result["error"]["recovery_action"]
