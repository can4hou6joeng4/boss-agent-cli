"""Browser Bridge 客户端 — CLI 侧调用 daemon 的 HTTP 接口。"""

import json
import time

import httpx

from boss_agent_cli.bridge.protocol import (
	BRIDGE_HOST, BRIDGE_PORT,
	DAEMON_PING_PATH, DAEMON_STATUS_PATH, DAEMON_COMMAND_PATH,
	BridgeCommand, BridgeResult, make_command_id,
)


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
		except Exception:
			return False

	def status(self) -> dict | None:
		"""获取 daemon 状态。"""
		try:
			resp = httpx.get(
				f"{self._base_url}{DAEMON_STATUS_PATH}",
				timeout=2.0,
			)
			if resp.status_code == 200:
				return resp.json()
			return None
		except Exception:
			return None

	def is_extension_connected(self) -> bool:
		"""检查扩展是否已连接。"""
		st = self.status()
		return st is not None and st.get("extensionConnected", False)

	def send_command(self, action: str, **kwargs) -> BridgeResult:
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

	def evaluate(self, code: str, *, workspace: str = "boss") -> dict:
		"""在页面上下文中执行 JS，返回结果。"""
		result = self.send_command("exec", code=code, workspace=workspace)
		if not result.ok:
			raise RuntimeError(f"Bridge evaluate 失败: {result.error}")
		return result.data if isinstance(result.data, dict) else {"result": result.data}

	def navigate(self, url: str, *, workspace: str = "boss") -> dict:
		"""导航到指定 URL。"""
		result = self.send_command("navigate", url=url, workspace=workspace)
		if not result.ok:
			raise RuntimeError(f"Bridge navigate 失败: {result.error}")
		return result.data if isinstance(result.data, dict) else {}

	def get_cookies(self, domain: str) -> list[dict]:
		"""获取指定域名的 Cookie。"""
		result = self.send_command("cookies", domain=domain)
		if not result.ok:
			raise RuntimeError(f"Bridge get_cookies 失败: {result.error}")
		return result.data if isinstance(result.data, list) else []

	def close_window(self, *, workspace: str = "boss") -> None:
		"""关闭 automation window。"""
		self.send_command("close-window", workspace=workspace)

	def fetch_json(self, url: str, *, method: str = "GET", data: dict | None = None, referer: str = "", workspace: str = "boss") -> dict:
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
