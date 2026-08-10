"""DrissionPage transport used only by the explicit crawl command."""

from __future__ import annotations

import json
import random
import socket
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from boss_agent_cli.crawler.hooks import HookInjection, HookRegistrationError, inject_hook_profile

# 兼容 search / pc 等变体；监听只作主路径，失败时回退 httpx。
JOBLIST_TARGET = r"wapi/zpgeek/[^?\s]*joblist\.json"
JOBLIST_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
JOB_CARD_URL = "https://www.zhipin.com/wapi/zpgeek/job/card.json"


class CrawlRiskError(RuntimeError):
	"""The platform returned a risk signal and the run must checkpoint-stop."""


class CrawlTransport(Protocol):
	def open(self) -> list[HookInjection]: ...
	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict[str, Any]: ...
	def fetch_detail(self, security_id: str) -> dict[str, Any]: ...
	def close(self) -> None: ...


class DrissionCrawlerSession:
	"""A single-tab, sequential browser listener and page-cookie detail client."""

	def __init__(
		self,
		*,
		profile_path: Path,
		chrome_path: str | None,
		cdp_port: int,
		hook_profile: str,
		hook_dir: Path | None,
		timeout: int = 20,
		seed_cookies: dict[str, str] | None = None,
		user_agent: str | None = None,
	) -> None:
		self._profile_path = profile_path
		self._chrome_path = chrome_path
		self._cdp_port = cdp_port
		self._hook_profile = hook_profile
		self._hook_dir = hook_dir
		self._timeout = timeout
		self._seed_cookies = dict(seed_cookies or {})
		self._user_agent = user_agent or ""
		self._page: Any = None
		self._details: httpx.Client | None = None
		self._detail_stoken = str(self._seed_cookies.get("__zp_stoken__") or "")
		self._started = False

	def open(self) -> list[HookInjection]:
		try:
			from DrissionPage import ChromiumOptions, ChromiumPage
		except ImportError as exc:
			raise RuntimeError("crawl 需要 DrissionPage；请安装 boss-agent-cli[crawl]") from exc
		options = ChromiumOptions()
		self._profile_path = self._profile_path.resolve()
		self._profile_path.mkdir(parents=True, exist_ok=True)
		port_busy = self._port_is_in_use(self._cdp_port)
		if port_busy:
			# risk_stopped 后会故意保留采集 Chrome；恢复时附着同一调试端口，而不是报占用。
			options.set_local_port(self._cdp_port)
			options.existing_only(True)
		else:
			# 上次异常退出会留下 lock；端口空闲时清理，避免“闪一下就退出/起不来”。
			self._clear_stale_profile_locks(self._profile_path)
			if self._chrome_path:
				options.set_browser_path(self._chrome_path)
			options.set_local_port(self._cdp_port)
			options.set_user_data_path(str(self._profile_path))
		# 采集浏览器与日常 Chrome 隔离；必须注入 boss login 的 Cookie，否则首页是登录 HTML。
		self._page = ChromiumPage(options)
		if not port_busy:
			self._seed_session_cookies()
		else:
			# 附着已有窗口时仍刷新 Cookie，方便用户刚完成登录/验证。
			self._seed_session_cookies()
		results = inject_hook_profile(self._page, self._hook_profile, self._hook_dir)
		failed = [item for item in results if not item.success]
		if failed:
			raise HookRegistrationError(results)
		self._start_joblist_listener()
		return results

	def _start_joblist_listener(self) -> None:
		if self._page is None:
			return
		listen = getattr(self._page, "listen", None)
		if listen is None:
			return
		stop = getattr(listen, "stop", None)
		if callable(stop):
			try:
				stop()
			except Exception:
				pass
		listen.start(JOBLIST_TARGET, is_regex=True, method=("GET", "POST"))

	@staticmethod
	def _normalize_response_body(body: Any) -> Any:
		if isinstance(body, (dict, list)):
			return body
		if isinstance(body, (bytes, bytearray)):
			body = bytes(body).decode("utf-8", errors="replace")
		if isinstance(body, str):
			text = body.strip()
			if not text:
				return text
			try:
				return json.loads(text)
			except json.JSONDecodeError:
				return text
		return body

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict[str, Any]:
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		# 每次拉列表前重置监听，避免附着旧窗口时吃到过期/错误响应包。
		self._start_joblist_listener()
		if self._started:
			self._trigger_next_page()
		else:
			self._page.get(f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}")
			self._started = True
		self._raise_if_security_page()
		listen_error: Exception | None = None
		try:
			packet = self._page.listen.wait(timeout=self._timeout, raise_err=False)
			if packet is not None:
				body = self._normalize_response_body(getattr(getattr(packet, "response", None), "body", None))
				if isinstance(body, dict):
					self._raise_if_risk_response(body)
					return body
				listen_error = CrawlRiskError(self._non_json_risk_message(page_no, body))
			else:
				listen_error = TimeoutError(f"第 {page_no} 页等待 joblist.json 超时")
		except CrawlRiskError as exc:
			listen_error = exc
		except Exception as exc:
			listen_error = exc

		# 页面 UI 已登录时仍可能监听失败。优先在页面上下文 fetch（同浏览器指纹），
		# 再退到进程内 httpx（可能被环境检测 code=37）。
		errors: list[str] = []
		if listen_error is not None:
			errors.append(str(listen_error))
		try:
			payload = self._fetch_joblist_in_page(query=query, city_code=city_code, page_no=page_no)
			self._raise_if_risk_response(payload)
			return payload
		except Exception as page_exc:
			errors.append(f"页面内 fetch 失败：{page_exc}")
		try:
			payload = self._fetch_joblist_http(query=query, city_code=city_code, page_no=page_no)
			self._raise_if_risk_response(payload)
			return payload
		except Exception as http_exc:
			errors.append(f"HTTP 回退失败：{http_exc}")
		self._raise_if_security_page(require_job_list=True)
		raise CrawlRiskError("；".join(errors) if errors else f"第 {page_no} 页职位列表获取失败")

	def _fetch_joblist_in_page(self, *, query: str, city_code: str, page_no: int) -> dict[str, Any]:
		"""Request joblist inside the open tab (same cookies/fingerprint as visible UI).

		Note: DrissionPage.run_async_js does NOT await promises (returns immediately),
		so we use synchronous XHR via run_js, then CDP awaitPromise as a second try.
		"""
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		url = str(getattr(self._page, "url", "") or "")
		if "zhipin.com" not in url:
			self._page.get(f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}")

		# 1) sync XHR — works with run_js (async fetch + run_js yields NoneType).
		sync_script = """
			function (query, city, page) {
				const params = new URLSearchParams({
					query: String(query || ''),
					city: String(city || ''),
					page: String(page || 1),
				});
				const xhr = new XMLHttpRequest();
				xhr.open('GET', '/wapi/zpgeek/search/joblist.json?' + params.toString(), false);
				xhr.withCredentials = true;
				xhr.setRequestHeader('Accept', 'application/json, text/plain, */*');
				try {
					xhr.send(null);
				} catch (e) {
					return {status: 0, text: String(e), error: true};
				}
				return {status: xhr.status, text: xhr.responseText || ''};
			}
		"""
		raw = None
		run_js = getattr(self._page, "run_js", None)
		if callable(run_js):
			try:
				raw = run_js(sync_script, query, city_code, page_no)
			except Exception:
				raw = None

		# 2) CDP evaluate + awaitPromise for async fetch if sync path unavailable.
		if raw is None:
			raw = self._fetch_joblist_via_cdp_evaluate(query=query, city_code=city_code, page_no=page_no)

		if isinstance(raw, str):
			try:
				raw = json.loads(raw)
			except json.JSONDecodeError as exc:
				raise CrawlRiskError(f"页面内请求返回无法解析：{raw[:160]}") from exc
		if not isinstance(raw, dict):
			raise CrawlRiskError(
				f"页面内请求返回类型异常：{type(raw).__name__}（async JS 未等待完成时常见为 None）"
			)
		status = int(raw.get("status") or 0)
		text = str(raw.get("text") or "")
		if raw.get("error") or status <= 0:
			raise CrawlRiskError(f"页面内请求失败：{text[:200]}")
		if status >= 400:
			raise CrawlRiskError(f"页面内请求 HTTP {status}：{text[:160]}")
		try:
			payload = json.loads(text)
		except json.JSONDecodeError as exc:
			preview = text[:160].replace("\n", " ")
			raise CrawlRiskError(f"页面内请求非 JSON：{preview}") from exc
		if not isinstance(payload, dict):
			raise CrawlRiskError("页面内请求响应不是对象")
		return payload

	def _fetch_joblist_via_cdp_evaluate(
		self, *, query: str, city_code: str, page_no: int
	) -> dict[str, Any] | None:
		"""Fallback: Runtime.evaluate with awaitPromise=true."""
		if self._page is None:
			return None
		run_cdp = getattr(self._page, "run_cdp", None)
		if not callable(run_cdp):
			return None
		expression = f"""
		(async () => {{
			const params = new URLSearchParams({{
				query: {json.dumps(query)},
				city: {json.dumps(city_code)},
				page: {json.dumps(str(page_no))},
			}});
			const resp = await fetch('/wapi/zpgeek/search/joblist.json?' + params.toString(), {{
				method: 'GET',
				credentials: 'include',
				headers: {{ 'Accept': 'application/json, text/plain, */*' }},
			}});
			const text = await resp.text();
			return JSON.stringify({{ status: resp.status, text }});
		}})()
		"""
		try:
			result = run_cdp(
				"Runtime.evaluate",
				expression=expression,
				awaitPromise=True,
				returnByValue=True,
			)
		except Exception:
			return None
		if not isinstance(result, dict):
			return None
		value = (result.get("result") or {}).get("value")
		if isinstance(value, str):
			try:
				parsed = json.loads(value)
			except json.JSONDecodeError:
				return None
			return parsed if isinstance(parsed, dict) else None
		if isinstance(value, dict):
			return value
		return None

	def _fetch_joblist_http(self, *, query: str, city_code: str, page_no: int) -> dict[str, Any]:
		"""Direct joblist request using cookies from the crawl browser session."""
		# Force rebuild client so cookies match the kept-open browser after user actions.
		if self._details is not None:
			self._details.close()
			self._details = None
		client = self._detail_client()
		params: dict[str, Any] = {
			"query": query,
			"city": city_code,
			"page": page_no,
		}
		if self._detail_stoken:
			params["__zp_stoken__"] = self._detail_stoken
		response = client.get(JOBLIST_URL, params=params)
		response.raise_for_status()
		try:
			payload = response.json()
		except json.JSONDecodeError as exc:
			preview = (response.text or "")[:180].replace("\n", " ")
			raise CrawlRiskError(
				f"HTTP 拉取 joblist 仍非 JSON（HTTP {response.status_code}）：{preview}"
			) from exc
		if not isinstance(payload, dict):
			raise CrawlRiskError("HTTP 拉取 joblist 响应不是对象")
		return payload

	def fetch_detail(self, security_id: str) -> dict[str, Any]:
		if not security_id:
			return {"code": 0, "zpData": {"jobCard": {}}}
		client = self._detail_client()
		params = {"securityId": security_id}
		if self._detail_stoken:
			params["__zp_stoken__"] = self._detail_stoken
		response = client.get(JOB_CARD_URL, params=params)
		response.raise_for_status()
		payload = response.json()
		if not isinstance(payload, dict):
			raise RuntimeError("job_card 响应不是对象")
		self._raise_if_risk_response(payload)
		return payload

	def close(self, *, quit_browser: bool = True) -> None:
		"""Release helpers; optionally leave Chrome running for human verification."""
		if self._details is not None:
			self._details.close()
			self._details = None
		if self._page is not None:
			if quit_browser:
				quit_browser_fn = getattr(self._page, "quit", None)
				if callable(quit_browser_fn):
					try:
						quit_browser_fn()
					except Exception:
						pass
			self._page = None

	def _detail_client(self) -> httpx.Client:
		if self._details is not None:
			return self._details
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		cookies: dict[str, str] = {}
		for cookie in self._page.cookies():
			name = cookie.get("name")
			value = cookie.get("value")
			if name and value:
				cookies[str(name)] = str(value)
			if name == "__zp_stoken__" and value:
				self._detail_stoken = str(value)
		self._details = httpx.Client(
			cookies=cookies,
			headers={
				"User-Agent": str(getattr(self._page, "user_agent", "")),
				"Referer": "https://www.zhipin.com/web/geek/job",
				"Accept": "application/json, text/plain, */*",
			},
			follow_redirects=True,
			timeout=20,
		)
		return self._details

	def _trigger_next_page(self) -> None:
		if self._page is None:
			return
		for _ in range(2):
			self._page.scroll.down(800)
			time.sleep(random.uniform(1.0, 2.0))
		self._page.scroll.to_bottom()
		time.sleep(random.uniform(2.5, 5.0))

	@staticmethod
	def _port_is_in_use(port: int) -> bool:
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
			connection.settimeout(0.2)
			return connection.connect_ex(("127.0.0.1", port)) == 0

	def _seed_session_cookies(self) -> None:
		"""Inject boss-login cookies into the isolated crawl profile."""
		if self._page is None or not self._seed_cookies:
			return
		# Ensure domain context before setting cookies.
		try:
			self._page.get("https://www.zhipin.com/")
		except Exception:
			pass
		cookie_items = [
			{
				"name": str(name),
				"value": str(value),
				"domain": ".zhipin.com",
				"path": "/",
			}
			for name, value in self._seed_cookies.items()
			if name and value is not None
		]
		if not cookie_items:
			return
		set_cookies = getattr(getattr(self._page, "set", None), "cookies", None)
		if callable(set_cookies):
			try:
				set_cookies(cookie_items)
				return
			except Exception:
				pass
		# Fallback: one-by-one for older DrissionPage APIs.
		for item in cookie_items:
			try:
				if callable(set_cookies):
					set_cookies(item)
			except Exception:
				continue

	@staticmethod
	def _clear_stale_profile_locks(profile_path: Path) -> None:
		for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
			path = profile_path / name
			try:
				if path.exists() or path.is_symlink():
					path.unlink()
			except OSError:
				pass

	def _non_json_risk_message(self, page_no: int, body: Any) -> str:
		text = body if isinstance(body, str) else str(body)
		lower = text[:2000].lower()
		url = str(getattr(self._page, "url", "")).lower() if self._page is not None else ""
		if self._looks_like_login(url, lower):
			return (
				f"第 {page_no} 页未拿到职位接口 JSON（页面像是登录页）。"
				"采集使用独立 Chrome 配置目录，已尝试注入 boss login 的 Cookie；"
				"请确认已执行 boss login，或重新登录后再恢复采集。"
			)
		if "zhipin-security" in lower or "zhipin-security" in url:
			return f"第 {page_no} 页触发安全验证页，已停止以避免继续触发验证"
		return (
			f"第 {page_no} 页 joblist.json 返回非 JSON 内容，已停止。"
			"常见原因：采集浏览器未登录、页面被重定向，或触发了安全校验。"
		)

	@staticmethod
	def _looks_like_login(url: str, html: str) -> bool:
		markers = (
			"web/user",
			"/login",
			"扫码登录",
			"手机号登录",
			"qrcode",
			"登录/注册",
			"请登录",
		)
		blob = f"{url}\n{html}"
		return any(marker in blob for marker in markers)

	def _raise_if_security_page(self, *, require_job_list: bool = False) -> None:
		if self._page is None:
			return
		url = str(getattr(self._page, "url", "")).lower()
		html = str(getattr(self._page, "html", ""))[:4000].lower()
		if self._looks_like_login(url, html):
			raise CrawlRiskError(
				"采集浏览器当前未登录。请先 boss login（采集会注入登录 Cookie），"
				"或手动在采集 profile 中完成登录后再恢复。"
			)
		if "zhipin-security" in url or "zhipin-security" in html:
			raise CrawlRiskError("检测到 zhipin-security 安全页")
		if not require_job_list:
			return
		find = getattr(self._page, "ele", None)
		if not callable(find):
			return
		try:
			if find(".job-list-box", timeout=0) is None and find(".job-card-wrapper", timeout=0) is None:
				raise CrawlRiskError("页面缺少职位列表容器，疑似未登录或安全页")
		except CrawlRiskError:
			raise
		except Exception:
			# 页面尚未支持元素探测时保留普通超时语义，不能把探测错误误判为风险。
			return

	@staticmethod
	def _raise_if_risk_response(payload: dict[str, Any]) -> None:
		code = payload.get("code")
		if code in (37, 38):
			message = str(payload.get("message") or "")
			# code=37 常见文案「环境存在异常」：自动化/独立 profile 指纹问题，不等于账号被封。
			hint = (
				"这通常是采集浏览器环境指纹被判定异常（与账号是否被封不是一回事）。"
				"可尝试：在保持打开的采集窗口中手动打开职位列表刷新，再点「继续采集」；"
				"或换用日常 Chrome + 调试端口，减少独立 headless profile 特征。"
			)
			raise CrawlRiskError(f"平台返回风险码 code={code}: {message}。{hint}")
