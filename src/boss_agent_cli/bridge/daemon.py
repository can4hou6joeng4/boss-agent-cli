"""Browser Bridge daemon — HTTP + WebSocket 服务。

接收 CLI 的 HTTP 命令，转发给 Chrome 扩展（WebSocket），返回结果。
首次浏览器命令时自动启动，空闲 4h 后自动退出。
"""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

# daemon 需要 websockets 和 aiohttp，但作为可选依赖
# 只在实际启动 daemon 时导入

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
		# 等待进程退出
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


def start_daemon_background() -> int:
	"""在后台启动 daemon 进程，返回 PID。"""
	if is_daemon_running():
		return get_daemon_pid()

	_ensure_dirs()

	# Fork 一个子进程运行 daemon
	pid = os.fork()
	if pid > 0:
		# 父进程：等待一小段时间确认启动
		time.sleep(0.5)
		return pid

	# 子进程：脱离终端
	os.setsid()
	pid2 = os.fork()
	if pid2 > 0:
		os._exit(0)

	# 孙进程：真正的 daemon
	# 重定向 stdio
	sys.stdin.close()
	log_fd = open(_LOG_FILE, "a")
	os.dup2(log_fd.fileno(), sys.stdout.fileno())
	os.dup2(log_fd.fileno(), sys.stderr.fileno())

	# 写 PID 文件
	_PID_FILE.write_text(str(os.getpid()))

	# 运行 asyncio 事件循环
	try:
		asyncio.run(_run_daemon())
	finally:
		_PID_FILE.unlink(missing_ok=True)
		os._exit(0)


async def _run_daemon():
	"""daemon 主循环：HTTP 服务 + WebSocket 服务。"""
	from aiohttp import web
	import websockets.server

	from boss_agent_cli.bridge.protocol import (
		BRIDGE_HOST, BRIDGE_PORT, DAEMON_IDLE_TIMEOUT,
		DAEMON_WS_PATH, DAEMON_PING_PATH, DAEMON_STATUS_PATH, DAEMON_COMMAND_PATH,
	)

	# 状态
	ext_ws = None
	ext_version = None
	last_activity = time.time()
	pending_commands: dict[str, asyncio.Future] = {}

	# ── WebSocket handler（Chrome 扩展连接） ──────────────────────

	async def ws_handler(websocket):
		nonlocal ext_ws, ext_version
		ext_ws = websocket
		print(f"[bridge] 扩展已连接: {websocket.remote_address}", flush=True)
		try:
			async for message in websocket:
				data = json.loads(message)
				msg_type = data.get("type")

				if msg_type == "hello":
					ext_version = data.get("version", "unknown")
					print(f"[bridge] 扩展版本: {ext_version}", flush=True)
					continue

				if msg_type == "log":
					level = data.get("level", "info")
					msg = data.get("msg", "")
					print(f"[bridge:ext:{level}] {msg}", flush=True)
					continue

				# 命令结果
				cmd_id = data.get("id")
				if cmd_id and cmd_id in pending_commands:
					pending_commands[cmd_id].set_result(data)
		except Exception as e:
			print(f"[bridge] 扩展断连: {e}", flush=True)
		finally:
			if ext_ws is websocket:
				ext_ws = None
				ext_version = None
			print("[bridge] 扩展已断开", flush=True)

	# ── HTTP handler ──────────────────────────────────────────────

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
		nonlocal last_activity, ext_ws
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
			await ext_ws.send(json.dumps(cmd))
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

	# ── 启动服务 ──────────────────────────────────────────────────

	start_time = time.time()

	# HTTP server
	app = web.Application()
	app.router.add_get(DAEMON_PING_PATH, handle_ping)
	app.router.add_get(DAEMON_STATUS_PATH, handle_status)
	app.router.add_post(DAEMON_COMMAND_PATH, handle_command)

	runner = web.AppRunner(app, access_log=None)
	await runner.setup()
	site = web.TCPSite(runner, BRIDGE_HOST, BRIDGE_PORT)
	await site.start()
	print(f"[bridge] HTTP 服务启动: http://{BRIDGE_HOST}:{BRIDGE_PORT}", flush=True)

	# WebSocket server（独立端口或同端口不同路径）
	# 使用 aiohttp 的 WebSocket 支持替代 websockets 库
	ws_app = web.Application()

	async def ws_upgrade_handler(request):
		ws = web.WebSocketResponse()
		await ws.prepare(request)
		await _ws_handler_aiohttp(ws, ext_ws_holder={})
		return ws

	# 用 aiohttp 内置 WebSocket 替代 websockets 库，减少依赖
	# 重写为统一 HTTP server 处理 WS 升级
	# 这里简化：在同一个 app 上加 WS 路由

	# 清理之前的方案，用 aiohttp 统一处理
	await runner.cleanup()

	# 重建统一 app
	unified_app = web.Application()
	unified_app.router.add_get(DAEMON_PING_PATH, handle_ping)
	unified_app.router.add_get(DAEMON_STATUS_PATH, handle_status)
	unified_app.router.add_post(DAEMON_COMMAND_PATH, handle_command)

	async def ws_aiohttp_handler(request):
		nonlocal ext_ws, ext_version
		ws = web.WebSocketResponse()
		await ws.prepare(request)
		ext_ws = ws
		print(f"[bridge] 扩展已连接 (aiohttp WS)", flush=True)

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

					cmd_id = data.get("id")
					if cmd_id and cmd_id in pending_commands:
						pending_commands[cmd_id].set_result(data)
				elif msg.type == web.WSMsgType.ERROR:
					print(f"[bridge] WS error: {ws.exception()}", flush=True)
		finally:
			if ext_ws is ws:
				ext_ws = None
				ext_version = None
			print("[bridge] 扩展已断开", flush=True)

		return ws

	unified_app.router.add_get(DAEMON_WS_PATH, ws_aiohttp_handler)

	unified_runner = web.AppRunner(unified_app, access_log=None)
	await unified_runner.setup()
	unified_site = web.TCPSite(unified_runner, BRIDGE_HOST, BRIDGE_PORT)
	await unified_site.start()
	print(f"[bridge] 统一服务启动: http://{BRIDGE_HOST}:{BRIDGE_PORT}", flush=True)
	print(f"[bridge] PID: {os.getpid()}, 空闲超时: {DAEMON_IDLE_TIMEOUT}s", flush=True)

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
		await unified_runner.cleanup()
		print("[bridge] daemon 已停止", flush=True)
