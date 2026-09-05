"""私有收尾进程的离线集成测试；仅允许本地 HTTP 测试服务器。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from boss_agent_cli.commands.recruiter import _read_receipt_worker as worker


def _arguments(tmp_path: Path) -> dict:
	return {
		"data_dir": tmp_path, "platform_name": "zhipin", "delay": (0, 0), "cdp_url": None,
		"start_result": {"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123, "unread": 0}},
		"geek_id": "geek", "job_id": "job", "security_id": "security", "timeout": 10,
	}


def test_real_worker_returns_zero_unread_without_auth_or_network(tmp_path: Path) -> None:
	assert worker.run_read_receipt(**_arguments(tmp_path)) == {"status": "not_needed", "unread": 0}


def test_real_worker_preserves_auth_error_without_login(tmp_path: Path) -> None:
	arguments = _arguments(tmp_path)
	arguments["start_result"] = {"code": 0}
	state = worker.run_read_receipt(**arguments)
	assert state["error_code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("response", ["not-json", "[]", '{"status":"invalid"}'])
def test_worker_invalid_output_is_redacted(tmp_path: Path, response: str) -> None:
	with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "sensitive diagnostic")):
		state = worker.run_read_receipt(**_arguments(tmp_path))
	assert state == {"status": "failed", "reason": "read_receipt_worker_failed", "error_code": "NETWORK_ERROR"}


def test_worker_receives_native_context_on_stdin_not_command_line(tmp_path: Path) -> None:
	arguments = _arguments(tmp_path)
	arguments.update(allow_mqtt_session=True, delay=(1, 2), cdp_url="http://127.0.0.1:9222")
	with patch.object(worker.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"status":"published"}', "")) as run:
		assert worker.run_read_receipt(**arguments)["status"] == "published"
	assert run.call_args.args[0] == [sys.executable, "-m", "boss_agent_cli.commands.recruiter._read_receipt_worker"]
	payload = json.loads(run.call_args.kwargs["input"])
	assert payload["data_dir"] == str(tmp_path)
	assert payload["delay"] == [1, 2]
	assert payload["allow_mqtt_session"] is True
	assert payload["cdp_url"] == arguments["cdp_url"]
	assert 0 < run.call_args.kwargs["timeout"] <= arguments["timeout"]


def test_total_budget_includes_worker_startup(tmp_path: Path) -> None:
	arguments = _arguments(tmp_path)
	arguments["timeout"] = 1.5
	real_run, real_popen = subprocess.run, subprocess.Popen
	processes = []

	def start_process(*args, **kwargs):
		process = real_popen(*args, **kwargs)
		processes.append(process)
		return process

	# 模拟导入阶段阻塞，尚未进入收尾逻辑；短预算仍须杀死并回收进程。
	with patch.object(worker.subprocess, "run", side_effect=lambda *args, **kwargs: real_run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)), patch.object(worker.subprocess, "Popen", side_effect=start_process):
		started = time.monotonic()
		state = worker.run_read_receipt(**arguments)
		elapsed = time.monotonic() - started
	assert state == {"status": "timeout", "reason": "read_receipt_timeout"}
	assert elapsed < 4
	assert len(processes) == 1 and processes[0].poll() is not None


class _SlowResponse(BaseHTTPRequestHandler):
	def do_GET(self) -> None:
		self.send_response(200)
		self.send_header("Content-Length", "600")
		self.end_headers()
		try:
			for _ in range(600):
				self.wfile.write(b"x")
				self.wfile.flush()
				time.sleep(0.1)
		except (BrokenPipeError, ConnectionResetError):
			pass

	def log_message(self, format: str, *args: object) -> None:
		pass


@pytest.mark.parametrize("stage", ["dns", "http", "mqtt"])
def test_total_budget_kills_and_reaps_blocked_worker(tmp_path: Path, stage: str) -> None:
	server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowResponse)
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	thread.start()
	marker = tmp_path / "started"
	late_action = tmp_path / "late-action"
	# Run the real private entry point, replacing only its platform with an offline double.
	probe = f'''
import socket, time
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
from boss_agent_cli.commands.recruiter import _read_receipt_worker as worker
def blocked(*args, **kwargs):
    Path({str(marker)!r}).touch()
    if {stage!r} == "http":
        httpx.get("http://127.0.0.1:{server.server_port}", timeout=0.5, trust_env=False)
    elif {stage!r} == "dns":
        with patch.object(socket, "getaddrinfo", side_effect=lambda *a, **k: time.sleep(60)):
            socket.getaddrinfo("offline.invalid", 443)
    else:
        time.sleep(60)
    Path({str(late_action)!r}).touch()
    return {{"code": 0}}
platform = MagicMock()
platform.__enter__.return_value = platform
platform.is_success.side_effect = lambda result: result.get("code") == 0
platform.unwrap_data.side_effect = lambda result: result.get("zpData")
platform.friend_list.side_effect = blocked
platform.mark_read.side_effect = blocked
with patch.object(worker, "build_recruiter_platform_instance", return_value=platform):
    worker.main()
'''
	arguments = _arguments(tmp_path)
	# 冷启动导入可能超过 1.5 秒；须进入目标阶段，启动超时由独立用例覆盖。
	arguments.update(allow_mqtt_session=True)
	arguments["start_result"] = {"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123, "unread": 1, "messageId": 456}} if stage == "mqtt" else {"code": 0}
	real_run, real_popen = subprocess.run, subprocess.Popen
	processes = []

	def start_process(*args, **kwargs):
		process = real_popen(*args, **kwargs)
		processes.append(process)
		return process

	try:
		with patch.object(worker.subprocess, "run", side_effect=lambda *args, **kwargs: real_run([sys.executable, "-c", probe], **kwargs)), patch.object(worker.subprocess, "Popen", side_effect=start_process):
			started = time.monotonic()
			state = worker.run_read_receipt(**arguments)
			elapsed = time.monotonic() - started
		assert state == {"status": "timeout", "reason": "read_receipt_timeout"}
		assert marker.exists(), "The worker must reach the blocking stage before timeout"
		assert elapsed < arguments["timeout"] + 2
		assert len(processes) == 1 and processes[0].poll() is not None
		assert not late_action.exists()
	finally:
		server.shutdown()
		server.server_close()
		thread.join(timeout=2)
