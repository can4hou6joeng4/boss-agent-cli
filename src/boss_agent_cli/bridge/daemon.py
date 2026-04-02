"""Browser Bridge daemon — HTTP + WebSocket 服务。

接收 CLI 的 HTTP 命令，转发给 Chrome 扩展（WebSocket），返回结果。
首次浏览器命令时自动启动，空闲 4h 后自动退出。

依赖 aiohttp（可选依赖，仅 bridge extra 安装）。
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_PID_FILE = Path.home() / ".boss-agent" / "bridge" / "daemon.pid"
_LOG_FILE = Path.home() / ".boss-agent" / "bridge" / "daemon.log"


def _ensure_dirs():
	_PID_FILE.parent.mkdir(parents=True, exist_ok=True)


def is_daemon_running() -> bool:
	"""检查 daemon 是否在运行。"""
	if not _PID_FILE.exists():
		return False
	try:
		pid = int(_PID_FILE.read_text().strip())
		os.kill(pid, 0)
		return True
	except (OSError, ValueError):
		_PID_FILE.unlink(missing_ok=True)
		return False


def get_daemon_pid() -> int | None:
	"""获取 daemon PID，不运行则返回 None。"""
	if not _PID_FILE.exists():
		return None
	try:
		pid = int(_PID_FILE.read_text().strip())
		os.kill(pid, 0)
		return pid
	except (OSError, ValueError):
		_PID_FILE.unlink(missing_ok=True)
		return None


def stop_daemon() -> bool:
	"""停止 daemon 进程。"""
	pid = get_daemon_pid()
	if pid is None:
		return False
	try:
		os.kill(pid, signal.SIGTERM)
		for _ in range(20):
			try:
				os.kill(pid, 0)
				time.sleep(0.1)
			except OSError:
				break
		_PID_FILE.unlink(missing_ok=True)
		return True
	except OSError:
		_PID_FILE.unlink(missing_ok=True)
		return False


def start_daemon_background() -> int | None:
	"""在后台启动 daemon 进程，返回实际 daemon PID。跨平台兼容。"""
	if is_daemon_running():
		return get_daemon_pid()

	_ensure_dirs()

	# 跨平台后台启动：用 subprocess.Popen 替代 os.fork
	kwargs = {}
	if sys.platform == "win32":
		# Windows: DETACHED_PROCESS 标志
		kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
	else:
		# Unix: 新会话脱离终端
		kwargs["start_new_session"] = True

	log_fd = open(_LOG_FILE, "a")
	proc = subprocess.Popen(
		[sys.executable, "-m", "boss_agent_cli.bridge.daemon", "--serve"],
		stdout=log_fd,
		stderr=log_fd,
		stdin=subprocess.DEVNULL,
		**kwargs,
	)
	log_fd.close()

	# 等待 PID 文件出现（daemon 启动后写入）
	for _ in range(20):
		time.sleep(0.25)
		pid = get_daemon_pid()
		if pid is not None:
			return pid

	# fallback: 返回 Popen 的 PID
	return proc.pid


async def _run_daemon():
	"""daemon 主循环：统一 aiohttp HTTP + WebSocket 服务。"""
	from aiohttp import web

	from boss_agent_cli.bridge.protocol import (
		BRIDGE_HOST, BRIDGE_PORT, DAEMON_IDLE_TIMEOUT,
		DAEMON_WS_PATH, DAEMON_PING_PATH, DAEMON_STATUS_PATH, DAEMON_COMMAND_PATH,
	)

	start_time = time.time()
	ext_ws = None
	ext_version = None
	last_activity = time.time()
	pending_commands: dict[str, asyncio.Future] = {}

	# ── HTTP handlers ─────────────────────────────────────────────

	async def handle_ping(request):
		nonlocal last_activity
		last_activity = time.time()
		return web.json_response({"ok": True})

	async def handle_status(request):
		nonlocal last_activity
		last_activity = time.time()
		return web.json_response({
			"ok": True,
			"extensionConnected": ext_ws is not None,
			"extensionVersion": ext_version,
			"pid": os.getpid(),
			"uptime": int(time.time() - start_time),
		})

	async def handle_command(request):
		nonlocal last_activity
		last_activity = time.time()

		if ext_ws is None:
			return web.json_response(
				{"id": "", "ok": False, "error": "Extension not connected"},
				status=503,
			)

		try:
			cmd = await request.json()
		except Exception:
			return web.json_response(
				{"id": "", "ok": False, "error": "Invalid JSON"},
				status=400,
			)

		cmd_id = cmd.get("id", "")
		future = asyncio.get_event_loop().create_future()
		pending_commands[cmd_id] = future

		try:
			await ext_ws.send_json(cmd)
			result = await asyncio.wait_for(future, timeout=30.0)
			return web.json_response(result)
		except asyncio.TimeoutError:
			return web.json_response(
				{"id": cmd_id, "ok": False, "error": "Command timed out (30s)"},
				status=504,
			)
		except Exception as e:
			return web.json_response(
				{"id": cmd_id, "ok": False, "error": f"Send failed: {e}"},
				status=502,
			)
		finally:
			pending_commands.pop(cmd_id, None)

	# ── WebSocket handler（Chrome 扩展连接） ──────────────────────

	async def ws_handler(request):
		nonlocal ext_ws, ext_version
		ws = web.WebSocketResponse()
		await ws.prepare(request)
		ext_ws = ws
		print(f"[bridge] 扩展已连接", flush=True)

		try:
			async for msg in ws:
				if msg.type == web.WSMsgType.TEXT:
					data = json.loads(msg.data)
					msg_type = data.get("type")

					if msg_type == "hello":
						ext_version = data.get("version", "unknown")
						print(f"[bridge] 扩展版本: {ext_version}", flush=True)
						continue

					if msg_type == "log":
						level = data.get("level", "info")
						log_msg = data.get("msg", "")
						print(f"[bridge:ext:{level}] {log_msg}", flush=True)
						continue

					# 命令结果
					cmd_id = data.get("id")
					if cmd_id and cmd_id in pending_commands:
						pending_commands[cmd_id].set_result(data)
				elif msg.type == web.WSMsgType.ERROR:
					print(f"[bridge] WS error: {ws.exception()}", flush=True)
		finally:
			if ext_ws is ws:
				ext_ws = None
				ext_version = None
				# 扩展断连：取消所有 pending 命令
				for cmd_id, fut in list(pending_commands.items()):
					if not fut.done():
						fut.set_result({"id": cmd_id, "ok": False, "error": "Extension disconnected"})
			print("[bridge] 扩展已断开", flush=True)

		return ws

	# ── 启动统一服务 ──────────────────────────────────────────────

	app = web.Application()
	app.router.add_get(DAEMON_PING_PATH, handle_ping)
	app.router.add_get(DAEMON_STATUS_PATH, handle_status)
	app.router.add_post(DAEMON_COMMAND_PATH, handle_command)
	app.router.add_get(DAEMON_WS_PATH, ws_handler)

	runner = web.AppRunner(app, access_log=None)
	await runner.setup()
	site = web.TCPSite(runner, BRIDGE_HOST, BRIDGE_PORT)
	await site.start()
	print(f"[bridge] 服务启动: http://{BRIDGE_HOST}:{BRIDGE_PORT}", flush=True)
	print(f"[bridge] PID: {os.getpid()}, 空闲超时: {DAEMON_IDLE_TIMEOUT}s", flush=True)

	# 写 PID 文件（服务启动后再写，确保端口可用）
	_PID_FILE.write_text(str(os.getpid()))

	# ── 空闲超时检查 ──────────────────────────────────────────────

	try:
		while True:
			await asyncio.sleep(60)
			idle = time.time() - last_activity
			if idle > DAEMON_IDLE_TIMEOUT and ext_ws is None:
				print(f"[bridge] 空闲 {int(idle)}s 且无扩展连接，自动退出", flush=True)
				break
	except asyncio.CancelledError:
		pass
	finally:
		_PID_FILE.unlink(missing_ok=True)
		await runner.cleanup()
		print("[bridge] daemon 已停止", flush=True)


# ── CLI 入口：python -m boss_agent_cli.bridge.daemon --serve ────────

if __name__ == "__main__":
	if "--serve" in sys.argv:
		_ensure_dirs()
		try:
			asyncio.run(_run_daemon())
		except KeyboardInterrupt:
			pass
		finally:
			_PID_FILE.unlink(missing_ok=True)
