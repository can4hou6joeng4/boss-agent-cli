"""DrissionPage transport used only by the explicit crawl command."""

from __future__ import annotations

import json
import random
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, ContextManager, Protocol
from urllib.parse import quote, urlsplit

from boss_agent_cli.api.zhipin_errors import normalize_risk_code, platform_risk_page_response
from boss_agent_cli.api.request_budget import (
	cancellable_sleep,
	check_request_control,
	is_request_stop,
	retry_after_seconds,
)
from boss_agent_cli.crawler.hooks import HookInjection, HookRegistrationError, inject_hook_profile
from boss_agent_cli.display import risk_error_message

# 兼容 search / pc 等变体；监听只作主路径，失败时退到页面内同源请求（不走 httpx）。
JOBLIST_TARGET = r"wapi/zpgeek/[^?\s]*joblist\.json"
JOBLIST_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
JOB_CARD_URL = "https://www.zhipin.com/wapi/zpgeek/job/card.json"
JOBLIST_PATH = "/wapi/zpgeek/search/joblist.json"
JOB_CARD_PATH = "/wapi/zpgeek/job/card.json"

_CRAWL_FETCH_SCRIPT = """
async function (path, params, timeoutMs) {
	const controller = new AbortController();
	let timer;
	const deadline = new Promise((_, reject) => {
		timer = setTimeout(() => {
			controller.abort();
			reject(new Error('crawl request deadline exceeded'));
		}, timeoutMs);
	});
	try {
		return await Promise.race([deadline, (async () => {
			const query = new URLSearchParams(params || {});
			const resp = await fetch(path + '?' + query.toString(), {
				method: 'GET', credentials: 'include', signal: controller.signal,
				headers: {'Accept': 'application/json, text/plain, */*'},
			});
			if (resp.status === 429) return JSON.stringify({status: 429, text: '',
				retry_after: resp.headers.get('Retry-After') || ''});
			const text = await resp.text();
			return JSON.stringify({status: resp.status, text, url: resp.url,
				content_type: resp.headers.get('Content-Type') || '',
				retry_after: resp.headers.get('Retry-After') || ''});
		})()]);
	} catch (e) {
		return JSON.stringify({status: 0, error: true, interrupted: true});
	} finally {
		clearTimeout(timer);
	}
}
"""


class CrawlRiskError(RuntimeError):
	"""The platform returned a risk signal and the run must checkpoint-stop."""

	def __init__(self, message: str, *, reason: str = "ACCOUNT_RISK", platform_code: int | None = None) -> None:
		self.reason = reason
		self.platform_code = platform_code
		super().__init__(message)

	@classmethod
	def from_response(cls, payload: dict[str, Any]) -> "CrawlRiskError":
		"""Carry an observed business code explicitly; never infer it from a message."""
		classification_code = normalize_risk_code(payload.get("code"))
		code = normalize_risk_code(payload.get("__cli_platform_code__", classification_code))
		reason = "ENVIRONMENT_RISK" if classification_code in (31, 37) else "ACCOUNT_RISK"
		cause = "身份资料流程" if code == 38 else "风险流程"
		hint = "当前采集已停止并保存断点；请在官方目标业务页确认恢复后，由用户执行 boss clean --risk-lock 再显式继续。"
		# 平台 message 可能带临时凭据，不进入异常文本与运行结果。
		message = risk_error_message(reason)
		return cls(f"平台返回{cause} code={code}: {message}。{hint}", reason=reason, platform_code=code)


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
		# 页签可见列表当前停在第几页；None 表示本会话尚未导航或位置已不可信。
		# 断点恢复会带着 next_page>1 进入，必须据此先把页签对齐到目标页
		# （Issue #897）。
		self._page_no: int | None = None
		self._attempt_callback: Callable[[str], ContextManager[None]] | None = None
		self._cooldown_callback: Callable[[float], None] | None = None

	def bind_attempts(
		self, callback: Callable[[str], ContextManager[None]], cooldown: Callable[[float], None] | None = None
	) -> None:
		"""The service counts each navigation, pagination action and active fetch."""
		self._attempt_callback = callback
		self._cooldown_callback = cooldown

	@contextmanager
	def _attempt(self, kind: str) -> Iterator[None]:
		callback = getattr(self, "_attempt_callback", None)
		with callback(kind) if callback is not None else nullcontext():
			check_request_control()
			self._raise_if_platform_risk_page()
			yield
			self._raise_if_platform_risk_page()

	def _record_rate_limit(self, value: object) -> None:
		callback = getattr(self, "_cooldown_callback", None)
		if callback is not None:
			callback(max(10.0, retry_after_seconds(value) or 0.0))

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
		self._raise_if_platform_risk_page()
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
		self._raise_if_platform_risk_page()
		target_page = max(1, int(page_no))
		# 每次拉列表前重置监听，避免附着旧窗口时吃到过期/错误响应包。
		self._start_joblist_listener()
		listen_error: Exception | None = None
		# 监听器拿到的是页签里「可见列表」发出的 joblist 请求，不保证就是本次
		# 请求的页码。断点恢复从 next_page>1 开始时，新页签（或保留的旧窗口）
		# 仍停在第 1 页；不先对齐就会把第 1 页响应记成断点页，并重复消耗
		# 请求预算（Issue #897）。同一页重试也要重新导航：上一次的页位置
		# 不能当作这一次已经请求过。
		navigating = self._page_no is None or target_page <= self._page_no
		try:
			with self._attempt("list-navigation" if navigating else "list-pagination"):
				try:
					position = self._page_no
					if position is None or target_page <= position:
						self._page.get(
							f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}"
						)
						position = 1
					while position < target_page:
						self._trigger_next_page()
						position += 1
				except BaseException:
					# 导航/滚动可能已让页签前进但没有完成：位置不可信，下次重新导航。
					self._page_no = None
					raise
				self._page_no = position
				self._raise_if_security_page()
				packet = self._page.listen.wait(timeout=self._timeout, raise_err=False)
				if packet is not None:
					response = getattr(packet, "response", None)
					body = self._normalize_response_body(getattr(response, "body", None))
					if isinstance(body, dict):
						self._raise_if_risk_response(body)
					response_url = getattr(response, "url", None) or getattr(packet, "url", "")
					page_risk = platform_risk_page_response(
						url=response_url if isinstance(response_url, str) else "",
						body=body if isinstance(body, str) else "",
					)
					if page_risk is not None:
						raise CrawlRiskError.from_response(page_risk)
					if getattr(response, "status", None) == 403:
						raise CrawlRiskError("职位监听请求 HTTP 403，已停止；请在官方目标业务页确认恢复")
					if (
						getattr(response, "status", None) == 429
						or isinstance(body, dict)
						and body.get("code") in (9, "9")
					):
						headers = getattr(response, "headers", {}) or {}
						self._record_rate_limit(headers.get("retry-after") or headers.get("Retry-After"))
						return {"code": 9, "message": "请求频率受限", "zpData": {}}
					if isinstance(body, dict):
						return body
					listen_error = ValueError("职位监听响应不是 JSON 对象")
				else:
					listen_error = TimeoutError(f"第 {page_no} 页等待 joblist.json 超时")
		except CrawlRiskError:
			raise
		except Exception as exc:
			if is_request_stop(exc):
				raise
			listen_error = exc

		# 页面 UI 已登录时仍可能监听失败：退到页面上下文请求（同浏览器指纹与 Cookie）。
		# 不再退到进程内 httpx——把浏览器的 Cookie + __zp_stoken__ 拷出去另发请求，
		# 会让这个 stoken 被判异常，之后页面里的请求也一律 code 37。
		errors: list[str] = []
		if listen_error is not None:
			errors.append(str(listen_error))
		self._raise_if_security_page()
		try:
			payload = self._fetch_joblist_in_page(query=query, city_code=city_code, page_no=page_no)
			self._raise_if_risk_response(payload)
			return payload
		except CrawlRiskError:
			raise
		except Exception as page_exc:
			if is_request_stop(page_exc):
				raise
			errors.append(f"页面内 fetch 失败：{page_exc}")
		self._raise_if_security_page(require_job_list=True)
		raise RuntimeError("；".join(errors) if errors else f"第 {page_no} 页职位列表获取失败")

	def _fetch_joblist_in_page(self, *, query: str, city_code: str, page_no: int) -> dict[str, Any]:
		"""Request joblist inside the open tab (same cookies/fingerprint as visible UI)."""
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		url = str(getattr(self._page, "url", "") or "")
		if "zhipin.com" not in url:
			with self._attempt("list-navigation"):
				self._page.get(f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}")
			# 导航回到列表第 1 页；显式页码请求不依赖可见页，但后续监听路径要知道位置。
			self._page_no = 1
		return self._fetch_json_in_page(
			JOBLIST_PATH,
			{"query": str(query or ""), "city": str(city_code or ""), "page": str(page_no or 1)},
		)

	def _fetch_json_in_page(self, path: str, params: dict[str, str]) -> dict[str, Any]:
		"""在已打开的 zhipin 页签里发同源 GET（浏览器自带 Cookie / 指纹，不经 httpx）。

		DrissionPage.run_js awaits the returned promise; run_async_js deliberately does not.
		Both the request/body and evaluation have deadlines; synchronous XHR is avoided.
		"""
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")

		self._raise_if_security_page()
		kind = "detail-fetch" if path == JOB_CARD_PATH else "list-fetch"
		run_js = getattr(self._page, "run_js", None)
		if callable(run_js):
			with self._attempt(kind):
				raw = run_js(_CRAWL_FETCH_SCRIPT, path, params, self._timeout * 1000, timeout=self._timeout + 1)
				if raw is not None:
					return self._decode_fetch_response(raw)

		# 2) CDP evaluate + awaitPromise for async fetch if sync path unavailable.
		if callable(getattr(self._page, "run_cdp", None)):
			with self._attempt(kind + "-cdp"):
				raw = self._fetch_json_via_cdp_evaluate(path, params)
				return self._decode_fetch_response(raw)
		raise RuntimeError("页面内请求没有可用的求值通道")

	def _decode_fetch_response(self, raw: Any) -> dict[str, Any]:
		if isinstance(raw, str):
			try:
				raw = json.loads(raw)
			except json.JSONDecodeError as exc:
				raise RuntimeError("页面内请求返回无法解析的传输结果") from exc
		if not isinstance(raw, dict):
			raise RuntimeError(f"页面内请求返回类型异常：{type(raw).__name__}（async JS 未等待完成时常见为 None）")
		status = int(raw.get("status") or 0)
		text = str(raw.get("text") or "")
		try:
			payload = json.loads(text)
		except json.JSONDecodeError:
			payload = None
		if isinstance(payload, dict):
			self._raise_if_risk_response(payload)
		page_risk = platform_risk_page_response(
			url=str(raw.get("url") or ""), content_type=str(raw.get("content_type") or ""), body=text
		)
		if page_risk is not None:
			raise CrawlRiskError.from_response(page_risk)
		if status == 403:
			raise CrawlRiskError("页面内请求 HTTP 403，已停止；请在官方页面核对后显式恢复")
		if raw.get("interrupted"):
			raise TimeoutError("页面内读取请求或响应体超时")
		if raw.get("error") or status <= 0:
			raise RuntimeError("页面内请求失败")
		if status == 429 or isinstance(payload, dict) and payload.get("code") in (9, "9"):
			self._record_rate_limit(raw.get("retry_after"))
			return {"code": 9, "message": "请求频率受限", "zpData": {}}
		if status >= 400:
			raise RuntimeError(f"页面内请求 HTTP {status}")
		if payload is None:
			raise RuntimeError("页面内请求响应不是 JSON")
		if not isinstance(payload, dict):
			raise RuntimeError("页面内请求响应不是对象")
		self._raise_if_risk_response(payload)
		return payload

	def _fetch_json_via_cdp_evaluate(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
		"""Fallback: Runtime.evaluate with awaitPromise=true."""
		if self._page is None:
			return None
		run_cdp = getattr(self._page, "run_cdp", None)
		if not callable(run_cdp):
			return None
		expression = f"({_CRAWL_FETCH_SCRIPT})({json.dumps(path)}, {json.dumps(params, ensure_ascii=False)}, {self._timeout * 1000})"
		result = run_cdp(
			"Runtime.evaluate",
			expression=expression,
			awaitPromise=True,
			returnByValue=True,
			timeout=(self._timeout + 1) * 1000,
			_timeout=self._timeout + 2,
		)
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

	def fetch_detail(self, security_id: str) -> dict[str, Any]:
		"""在采集页签里取 job_card（同源请求，不把浏览器 Cookie / stoken 拷给 httpx）。"""
		if not security_id:
			return {"code": 0, "zpData": {"jobCard": {}}}
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		payload = self._fetch_json_in_page(JOB_CARD_PATH, {"securityId": security_id})
		self._raise_if_risk_response(payload)
		return payload

	def close(self, *, quit_browser: bool = True) -> None:
		"""Release helpers; optionally leave Chrome running for human verification."""
		if self._page is not None:
			if quit_browser:
				quit_browser_fn = getattr(self._page, "quit", None)
				if callable(quit_browser_fn):
					try:
						quit_browser_fn()
					except Exception:
						pass
			self._page = None

	def _trigger_next_page(self) -> None:
		if self._page is None:
			return
		for _ in range(2):
			check_request_control()
			self._page.scroll.down(800)
			cancellable_sleep(random.uniform(1.0, 2.0))
		check_request_control()
		self._page.scroll.to_bottom()
		cancellable_sleep(random.uniform(2.5, 5.0))

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
			with self._attempt("seed-navigation"):
				self._page.get("https://www.zhipin.com/")
		except CrawlRiskError:
			raise
		except Exception as error:
			if is_request_stop(error):
				raise
		self._raise_if_platform_risk_page()
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
		self._raise_if_platform_risk_page()
		url = str(getattr(self._page, "url", "")).lower()
		html = str(getattr(self._page, "html", ""))[:4000].lower()
		if self._looks_like_login(url, html):
			raise CrawlRiskError(
				"采集浏览器当前未登录，已停止。请在官方页面完成登录并确认目标业务页恢复后，"
				"由用户执行 boss clean --risk-lock 再显式恢复。"
			)
		if urlsplit(url).path.rstrip("/") == "/web/user":
			raise CrawlRiskError(
				"采集浏览器进入身份资料流程，已停止；请在官方页面完成该流程并确认目标业务页恢复后显式继续"
			)
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

	def _raise_if_platform_risk_page(self) -> None:
		if self._page is None:
			return
		url = str(getattr(self._page, "url", ""))
		page_risk = platform_risk_page_response(url=url)
		if page_risk is None:
			html = str(getattr(self._page, "html", ""))[:4000]
			page_risk = platform_risk_page_response(url=url, content_type="text/html", body=html)
		if page_risk is not None:
			raise CrawlRiskError.from_response(page_risk)

	@staticmethod
	def _raise_if_risk_response(payload: dict[str, Any]) -> None:
		if normalize_risk_code(payload.get("code")) is not None:
			raise CrawlRiskError.from_response(payload)
