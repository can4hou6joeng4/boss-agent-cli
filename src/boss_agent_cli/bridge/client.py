"""Browser Bridge 客户端 — CLI 侧调用 daemon 的 HTTP 接口。"""

import json
import time
from typing import Any, cast

import httpx

from boss_agent_cli.bridge.protocol import (
	BRIDGE_HOST, BRIDGE_PORT,
	DAEMON_PING_PATH, DAEMON_STATUS_PATH, DAEMON_COMMAND_PATH,
	BridgeCommand, BridgeResult, make_command_id,
)

SUPPORTED_EXTENSION_MAJOR_VERSION = "1"
DIAGNOSTIC_NAVIGATE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}{DAEMON_PING_PATH}"


class BridgeNotRunning(Exception):
	pass


class BridgeExtensionDisconnected(Exception):
	pass


class BridgeClient:
	"""与 Bridge daemon 通信的 HTTP 客户端。"""

	def __init__(self, *, timeout: float = 30.0, max_retries: int = 4):
		self._base_url = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
		self._timeout = timeout
		self._max_retries = max_retries

	def is_running(self) -> bool:
		"""检查 daemon 是否在运行。"""
		try:
			resp = httpx.get(
				f"{self._base_url}{DAEMON_PING_PATH}",
				timeout=2.0,
			)
			return resp.status_code == 200
		except (httpx.HTTPError, OSError):
			return False

	def status(self) -> dict[str, Any] | None:
		"""获取 daemon 状态。"""
		try:
			resp = httpx.get(
				f"{self._base_url}{DAEMON_STATUS_PATH}",
				timeout=2.0,
			)
			if resp.status_code == 200:
				data = resp.json()
				if isinstance(data, dict):
					return cast("dict[str, Any]", data)
			return None
		except (httpx.HTTPError, ValueError, OSError):
			return None

	def is_extension_connected(self) -> bool:
		"""检查扩展是否已连接。"""
		st = self.status()
		return st is not None and st.get("extensionConnected", False)

	def diagnose(self, *, workspace: str = "boss", run_probes: bool = True) -> list[dict[str, Any]]:
		"""Return redacted local Bridge health checks for doctor output.

		The probes are local capability checks only. They do not read cookies,
		headers, platform pages, or account data.
		"""
		checks: list[dict[str, Any]] = []

		status = self.status()
		if status is None:
			checks.append(_bridge_check(
				"bridge_daemon",
				"warn",
				f"Bridge daemon 未运行或无法访问 http://{BRIDGE_HOST}:{BRIDGE_PORT}",
				"运行 boss doctor 前先启动 Bridge daemon，或检查 19826 端口是否被占用",
			))
			checks.append(_bridge_check(
				"bridge_extension",
				"warn",
				"Chrome 扩展尚未连接到本地 Bridge daemon",
				"安装并启用 extension/ 目录中的 BOSS Agent Bridge 扩展",
			))
			return checks

		pid = status.get("pid", "unknown")
		uptime = status.get("uptime", 0)
		checks.append(_bridge_check(
			"bridge_daemon",
			"ok",
			f"Bridge daemon 运行中 pid={pid} uptime={uptime}s",
			None,
			{"pid": pid, "uptime": uptime, "base_url": self._base_url},
		))

		extension_connected = bool(status.get("extensionConnected"))
		extension_version = str(status.get("extensionVersion") or "")
		checks.append(_bridge_check(
			"bridge_extension",
			"ok" if extension_connected else "warn",
			f"Chrome 扩展已连接 version={extension_version or 'unknown'}"
			if extension_connected else "Chrome 扩展未连接",
			None if extension_connected else "打开 chrome://extensions，安装并启用 extension/，再等待扩展连接 daemon",
			{"extension_version": extension_version or None},
		))

		if extension_connected:
			checks.append(_bridge_check(
				"bridge_protocol",
				"ok" if _is_supported_extension_version(extension_version) else "warn",
				f"扩展协议版本 {extension_version or 'unknown'}；CLI 期望 major={SUPPORTED_EXTENSION_MAJOR_VERSION}",
				None if _is_supported_extension_version(extension_version) else "重新加载与当前 CLI 匹配的 extension/ 扩展",
				{
					"extension_version": extension_version or None,
					"expected_major": SUPPORTED_EXTENSION_MAJOR_VERSION,
				},
			))
		else:
			checks.append(_bridge_check(
				"bridge_protocol",
				"warn",
				"扩展未连接，无法确认协议版本",
				"先连接扩展，再重新运行 boss doctor",
			))

		if not extension_connected or not run_probes:
			return checks

		exec_result = self.send_command(
			"exec",
			code="(() => ({ ok: true, href: location.href, title: document.title }))()",
			workspace=workspace,
		)
		if exec_result.ok:
			tab_data = exec_result.data if isinstance(exec_result.data, dict) else {}
			checks.append(_bridge_check(
				"bridge_workspace",
				"ok",
				_workspace_detail(tab_data),
				None,
				{
					"workspace": workspace,
					"tab_url": _safe_url(tab_data.get("href")),
					"title_present": bool(tab_data.get("title")),
				},
			))
			checks.append(_bridge_check(
				"bridge_exec",
				"ok",
				"Bridge exec 基础能力可用",
				None,
			))
		else:
			recovery = _bridge_recovery_for_error(exec_result.error, workspace=workspace)
			checks.append(_bridge_check(
				"bridge_workspace",
				"warn",
				f"无法确认 workspace/tab 状态: {exec_result.error or 'unknown error'}",
				recovery,
			))
			checks.append(_bridge_check(
				"bridge_exec",
				"warn",
				f"Bridge exec 基础能力不可用: {exec_result.error or 'unknown error'}",
				recovery,
			))

		fetch_result = self.send_command(
			"exec",
			code=(
				"fetch('data:application/json,{\"ok\":true}')"
				".then(resp => resp.json())"
			),
			workspace=workspace,
		)
		checks.append(_bridge_check(
			"bridge_fetch",
			"ok" if fetch_result.ok else "warn",
			"Bridge fetch 基础能力可用"
			if fetch_result.ok else f"Bridge fetch 基础能力不可用: {fetch_result.error or 'unknown error'}",
			None if fetch_result.ok else _bridge_recovery_for_error(fetch_result.error, workspace=workspace),
		))

		navigate_result = self.send_command(
			"navigate",
			url=DIAGNOSTIC_NAVIGATE_URL,
			workspace=f"{workspace}-diagnostic",
		)
		checks.append(_bridge_check(
			"bridge_navigate",
			"ok" if navigate_result.ok else "warn",
			"Bridge navigate 基础能力可用"
			if navigate_result.ok else f"Bridge navigate 基础能力不可用: {navigate_result.error or 'unknown error'}",
			None if navigate_result.ok else _bridge_recovery_for_error(navigate_result.error, workspace=workspace),
		))

		return checks

	def send_command(self, action: str, **kwargs: Any) -> BridgeResult:
		"""发送命令到扩展，自动重试。"""
		last_error = ""

		for attempt in range(1, self._max_retries + 1):
			cmd_id = make_command_id()
			cmd = BridgeCommand(id=cmd_id, action=action, **kwargs)

			try:
				resp = httpx.post(
					f"{self._base_url}{DAEMON_COMMAND_PATH}",
					json=cmd.to_dict(),
					timeout=self._timeout,
				)
				result = BridgeResult.from_dict(resp.json())

				if result.ok:
					return result

				# 可重试的错误
				err = result.error or ""
				is_transient = any(k in err for k in (
					"Extension disconnected",
					"Extension not connected",
					"attach failed",
					"no longer exists",
				))
				if is_transient and attempt < self._max_retries:
					time.sleep(1.5)
					continue

				last_error = err
				break

			except (httpx.ConnectError, httpx.TimeoutException) as e:
				last_error = str(e)
				if attempt < self._max_retries:
					time.sleep(0.5)
					continue
				break
			except Exception as e:
				last_error = str(e)
				break

		return BridgeResult(id="", ok=False, error=last_error)

	# ── 高级 API ─────────────────────────────────────────────────

	def evaluate(self, code: str, *, workspace: str = "boss") -> dict[str, Any]:
		"""在页面上下文中执行 JS，返回结果。"""
		result = self.send_command("exec", code=code, workspace=workspace)
		if not result.ok:
			raise RuntimeError(f"Bridge evaluate 失败: {result.error}")
		return result.data if isinstance(result.data, dict) else {"result": result.data}

	def navigate(self, url: str, *, workspace: str = "boss") -> dict[str, Any]:
		"""导航到指定 URL。"""
		result = self.send_command("navigate", url=url, workspace=workspace)
		if not result.ok:
			raise RuntimeError(f"Bridge navigate 失败: {result.error}")
		return result.data if isinstance(result.data, dict) else {}

	def get_cookies(self, domain: str) -> list[dict[str, Any]]:
		"""获取指定域名的 Cookie。"""
		result = self.send_command("cookies", domain=domain)
		if not result.ok:
			raise RuntimeError(f"Bridge get_cookies 失败: {result.error}")
		return result.data if isinstance(result.data, list) else []

	def close_window(self, *, workspace: str = "boss") -> None:
		"""关闭 automation window。"""
		self.send_command("close-window", workspace=workspace)

	def fetch_json(self, url: str, *, method: str = "GET", data: dict[str, Any] | None = None, referer: str = "", workspace: str = "boss") -> dict[str, Any]:
		"""通过浏览器 fetch() 发起 API 请求，返回 JSON。"""
		if method == "GET":
			js = f"""
				(async () => {{
					const resp = await fetch({json.dumps(url)}, {{
						method: 'GET',
						credentials: 'include',
						headers: {{
							'Accept': 'application/json',
							'X-Requested-With': 'XMLHttpRequest',
							{f"'Referer': {json.dumps(referer)}," if referer else ""}
						}},
					}});
					return await resp.json();
				}})()
			"""
		else:
			form_entries = "\n".join(
				f"formData.append({json.dumps(k)}, {json.dumps(str(v))});"
				for k, v in (data or {}).items()
			)
			js = f"""
				(async () => {{
					const formData = new URLSearchParams();
					{form_entries}
					const resp = await fetch({json.dumps(url)}, {{
						method: 'POST',
						credentials: 'include',
						headers: {{
							'Accept': 'application/json',
							'Content-Type': 'application/x-www-form-urlencoded',
							'X-Requested-With': 'XMLHttpRequest',
							{f"'Referer': {json.dumps(referer)}," if referer else ""}
						}},
						body: formData.toString(),
					}});
					return await resp.json();
				}})()
			"""
		return self.evaluate(js, workspace=workspace)


def _bridge_check(
	name: str,
	status: str,
	detail: str,
	recovery_action: str | None = None,
	extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
	check: dict[str, Any] = {
		"name": name,
		"status": status,
		"detail": detail,
	}
	if recovery_action:
		check["recovery_action"] = recovery_action
		check["hint"] = recovery_action
	if extra:
		check.update(extra)
	return check


def _is_supported_extension_version(version: str) -> bool:
	if not version:
		return False
	return version.split(".", 1)[0] == SUPPORTED_EXTENSION_MAJOR_VERSION


def _safe_url(value: Any) -> str | None:
	if not isinstance(value, str) or not value:
		return None
	if value.startswith(("http://", "https://")):
		return value.split("#", 1)[0]
	if value.startswith("data:"):
		return "data:<local>"
	return "<non-http>"


def _workspace_detail(data: dict[str, Any]) -> str:
	url = _safe_url(data.get("href"))
	if url:
		return f"Bridge workspace/tab 可用: {url}"
	return "Bridge workspace/tab 可用"


def _bridge_recovery_for_error(error: str, *, workspace: str) -> str:
	err = (error or "").lower()
	if "extension not connected" in err or "extension disconnected" in err:
		return "确认 Chrome 扩展已启用并连接本地 daemon，然后重新运行 boss doctor"
	if "cannot debug tab" in err or "url is" in err:
		return f"打开目标页面或允许 Bridge 创建 workspace={workspace} 的 http(s) 标签页"
	if "attach failed" in err or "another debugger" in err:
		return "关闭其他调试器或 DevTools 后重试；不要用 Bridge 重试平台风控拦截"
	if "timed out" in err:
		return "检查页面是否卡住，重新加载扩展或重启 Bridge daemon"
	return "检查 Bridge daemon、Chrome 扩展和目标页面状态后重试"
