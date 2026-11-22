"""DrissionPage transport used only by the explicit crawl command."""

from __future__ import annotations

import json
import os
import random
import socket
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Protocol
from urllib.parse import parse_qs, quote, urlsplit

from boss_agent_cli.api.zhipin_errors import normalize_risk_code, platform_risk_page_response
from boss_agent_cli.api.request_budget import (
	cancellable_sleep,
	check_request_control,
	is_request_stop,
	retry_after_seconds,
)
from boss_agent_cli.cache.store import CacheBusyError, CacheRecordInvalid
from boss_agent_cli.crawler.controls import (
	CrawlBrowserStartError,
	CrawlProfileBusy,
	CrawlProfilePathInvalid,
	CrawlRuntimeUnavailable,
	CrawlSessionAuthError,
)
from boss_agent_cli.crawler.hooks import HookInjection, HookRegistrationError, inject_hook_profile
from boss_agent_cli.display import risk_error_message
from boss_agent_cli.services.file_lock import exclusive_file_lock
from boss_agent_cli.services.install_extras import EXTRA_MODULES, extras_requirement, module_available

# 兼容 search / pc 等变体；监听只作主路径，失败时退到页面内同源请求（不走 httpx）。
JOBLIST_TARGET = r"wapi/zpgeek/[^?\s]*joblist\.json"
JOBLIST_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
JOB_CARD_URL = "https://www.zhipin.com/wapi/zpgeek/job/card.json"
JOBLIST_PATH = "/wapi/zpgeek/search/joblist.json"
JOB_CARD_PATH = "/wapi/zpgeek/job/card.json"


def _is_local_cache_error(exc: BaseException) -> bool:
	"""attempt 回调里抛出的本地缓存错误。

	预算 / 断点读写通过 ``bind_attempts`` 回调在本模块内执行；``CacheBusyError``、
	``CacheRecordInvalid`` 与 ``sqlite3.DatabaseError`` 重试同一次请求不会自愈，
	也不能被兜成可重试的传输错误（Issue #2324）。
	"""
	return isinstance(exc, (CacheBusyError, CacheRecordInvalid, sqlite3.DatabaseError))


def _joblist_query_params(packet: Any) -> dict[str, list[str]]:
	url = getattr(packet, "url", None)
	if not isinstance(url, str) or not url:
		url = getattr(getattr(packet, "request", None), "url", None)
	if not isinstance(url, str) or not url:
		return {}
	try:
		return parse_qs(urlsplit(url).query, keep_blank_values=True)
	except ValueError:
		return {}


def packet_page_no(packet: Any) -> int | None:
	"""从监听包的请求 URL 读页码，用于区分断点恢复时的各页响应。"""
	return _page_no_from_params(_joblist_query_params(packet))


def _page_no_from_params(params: dict[str, list[str]]) -> int | None:
	values = params.get("page")
	if values is None or len(values) != 1:
		return None
	try:
		page_no = int(values[0])
	except ValueError:
		return None
	return page_no if page_no >= 1 else None


def _browser_process_id(page: Any) -> int | None:
	"""返回 CDP 连接的浏览器主进程号；读不到（或类型漂移）时返回 None。"""
	process_id = getattr(getattr(page, "browser", None), "process_id", None)
	if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
		return None
	return process_id


def _browser_cmdline_profile(process_id: int) -> str | None:
	"""读浏览器进程命令行里的 ``--user-data-dir``；读不到返回 None。

	Windows 没有 POSIX 的 SingletonLock，附着校验只能靠命令行。psutil 随 crawl
	依赖（DrissionPage）传递安装，缺失或无权读取时按「无法确认」处理，由调用方
	决定是否拒绝。
	"""
	try:
		import psutil
	except ImportError:
		return None
	try:
		arguments = psutil.Process(process_id).cmdline()
	except (psutil.Error, OSError, ValueError):
		return None
	for argument in arguments:
		if argument.startswith("--user-data-dir="):
			return argument.split("=", 1)[1] or None
	return None


def _same_profile_directory(candidate: str, expected: Path) -> bool:
	try:
		return Path(candidate).expanduser().resolve(strict=False) == expected
	except (OSError, RuntimeError, ValueError):
		# 符号链接环（3.11 / 3.12 的 resolve 抛 RuntimeError）或含 NUL 等控制字符。
		return False


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


@dataclass(frozen=True)
class SessionSeedResult:
	"""本地 Cookie 注入结果；只保留数量、Cookie 名称与失败原因，不保留任何原值。"""

	attempted: int = 0
	injected: int = 0
	failures: tuple[str, ...] = ()
	navigation_error: str = ""

	@property
	def failed(self) -> bool:
		return bool(self.failures) or bool(self.navigation_error)

	@property
	def missing(self) -> int:
		return max(0, self.attempted - self.injected)


class CrawlTransport(Protocol):
	def open(self) -> list[HookInjection]: ...
	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict[str, Any]: ...
	def fetch_detail(self, security_id: str) -> dict[str, Any]: ...
	def close(self) -> None: ...


# 采集浏览器与 XLSX 导出各自依赖的可选包，都在 `crawl` 依赖组里。
_CRAWL_RUNTIME_MODULES = EXTRA_MODULES["crawl"]


def ensure_crawl_runtime() -> None:
	"""入口在创建任务、启动后台进程、打开浏览器之前确认 `crawl` 可选依赖已安装。

	默认安装不带这组依赖。等到打开浏览器才发现时任务已经建好，执行器只能把它
	记成 `stopped`，调用方拿到的是成功信封和恢复指引，照做只会重复同一个失败
	（Issue #1050）。这里只探测模块是否可导入，不导入它们。
	"""
	missing = [name for name in _CRAWL_RUNTIME_MODULES if not module_available(name)]
	if missing:
		raise CrawlRuntimeUnavailable(f"crawl 需要 {'、'.join(missing)}；请安装 {extras_requirement('crawl')}")


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
		# 监听器是否已成功启动：start 失败时 fetch_page 跳过监听等待，
		# 直接走页面内同源请求（Issue #2428）。
		self._listener_ready = False
		self._attempt_callback: Callable[[str], ContextManager[None]] | None = None
		self._cooldown_callback: Callable[[float], None] | None = None

	def minimum_list_requests(self, page_no: int, *, before_open: bool = False) -> int:
		"""计算列表导航和翻页所需的最少请求数，打开前也计入登录态初始化。"""
		position = None if before_open else self._page_no
		turns = page_no if position is None or page_no <= position else page_no - position
		return turns + int(before_open and bool(self._seed_cookies))

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
			raise CrawlRuntimeUnavailable("crawl 需要 DrissionPage；请安装 boss-agent-cli[crawl]") from exc
		options = ChromiumOptions()
		try:
			self._profile_path = self._profile_path.resolve()
			self._profile_path.mkdir(parents=True, exist_ok=True)
		except (OSError, RuntimeError, ValueError) as exc:
			# 路径被同名文件占用或不可写（OSError）、符号链接环（3.11 / 3.12 的
			# Path.resolve 抛 RuntimeError，3.13+ 不再抛），或含 NUL 等控制
			# 字符（ValueError）：确定性本地失败。原样上抛会被 _run 兜成
			# status=stopped + crawl resume，重跑永远复现（Issue #1290、#1482、#2169）。
			raise CrawlProfilePathInvalid(
				f"采集配置目录 {self._profile_path!r} 无法创建或不是目录（{exc}）；"
				"请检查该路径是否被同名文件占用、是否存在符号链接环、是否包含 NUL 等控制字符，"
				"或修复数据目录的写权限后重试"
			) from exc
		# 「读锁 → 清陈旧锁 → 启动」必须在跨进程锁内完成：两个 run 同时判定
		# 空闲时，后一个会清掉前一个刚写下的 SingletonLock 并抢同一个配置目录
		# （Issue #1266 的残余窗口）。
		with self._profile_open_lock():
			return self._open_browser(options, ChromiumPage)

	def _open_browser(self, options: Any, chromium_page: Any) -> list[HookInjection]:
		port_busy = self._port_is_in_use(self._cdp_port)
		if port_busy:
			# risk_stopped 后会故意保留采集 Chrome；恢复时附着同一调试端口，而不是报占用。
			options.set_local_port(self._cdp_port)
			options.existing_only(True)
		else:
			# 上次异常退出会留下 lock；端口空闲时清理，避免“闪一下就退出/起不来”。
			# 但端口按 run 随机分配，端口空闲不代表配置目录空闲：另一个 run 的采集
			# Chrome 可能正用同一个 profile，删掉它的 SingletonLock 会把两边都
			# 搞坏（Issue #1266）。锁里记录的进程还活着时不删锁、不起第二个实例。
			owner = self._profile_owner(self._profile_path)
			if owner is not None:
				raise CrawlProfileBusy(
					f"采集配置目录 {self._profile_path} 正被运行中的采集 Chrome（pid {owner}）使用；"
					"请等待该采集结束或先停止它，不要并发使用同一数据目录的采集浏览器，也不要删除 SingletonLock"
				)
			self._clear_stale_profile_locks(self._profile_path)
			if self._chrome_path:
				options.set_browser_path(self._chrome_path)
			if self._user_agent:
				# 采集 Cookie 是在登录浏览器的 UA 下取得的；新起的采集浏览器不声明
				# 同一个 UA，平台看到的登录态与指纹就不一致。参数一直从登录态透传
				# 到这里却从未应用，注入等于静默丢失（Issue #1413）。
				options.set_user_agent(self._user_agent)
			options.set_local_port(self._cdp_port)
			options.set_user_data_path(str(self._profile_path))
		# 采集浏览器与日常 Chrome 隔离；必须注入 boss login 的 Cookie，否则首页是登录 HTML。
		try:
			self._page = chromium_page(options)
		except Exception as exc:
			# 这里是唯一知道「浏览器没起来」的位置。原样上抛会被 service 的通用
			# except Exception 记成 stopped 并返回 ok:true（Issue #1553）：Chrome 未
			# 安装 / chrome_path 配错 / 调试端口被别的程序占用 / 附着窗口已关闭，
			# 都不是可恢复的采集中断，且本次没有发出任何平台请求。
			action = "附着到已有的采集浏览器" if port_busy else "启动采集浏览器"
			raise CrawlBrowserStartError(
				f"无法{action}（调试端口 {self._cdp_port}）：{type(exc).__name__}: {exc}",
				cdp_port=self._cdp_port,
				attached=port_busy,
			) from exc
		self._raise_if_platform_risk_page()
		# 端口上先有浏览器不代表它是采集 Chrome：日常 Chrome 或别的工具占着调试
		# 端口时，DrissionPage 会直接附着，Cookie 会被注入用户自己的浏览器。确认
		# 归属之前不注册 Hook、不注入 Cookie（Issue #2464）。
		self._verify_browser_profile_ownership(attached=port_busy)
		# Hook 必须在首个文档导航前注册：addScriptToEvaluateOnNewDocument 对已经
		# 加载的文档不生效，而 seed 导航就是第一个平台文档（Issue #1935）。
		results = inject_hook_profile(self._page, self._hook_profile, self._hook_dir)
		failed = [item for item in results if not item.success]
		if failed:
			raise HookRegistrationError(results, hook_dir=self._hook_dir)
		# 注入 boss login 的 Cookie（附着已有窗口时也刷新，方便用户刚完成登录/验证）。
		# 注入结果的失败信号必须在进入业务页之前消费：原来的 `pass`/`continue`
		# 把本地 CDP 失败吞掉，随后登录页被误判成平台风控并写风险锁（Issue #1405）。
		self._raise_if_seed_auth_failure(self._seed_session_cookies())
		self._start_joblist_listener()
		return results

	def _start_joblist_listener(self) -> None:
		if self._page is None:
			self._listener_ready = False
			return
		listen = getattr(self._page, "listen", None)
		if listen is None:
			self._listener_ready = False
			return
		stop = getattr(listen, "stop", None)
		if callable(stop):
			try:
				stop()
			except Exception:
				pass
		try:
			listen.start(JOBLIST_TARGET, is_regex=True, method=("GET", "POST"))
		except Exception:
			# 监听器不可用不阻止采集：fetch_page 会跳过监听等待，退到页面内
			# 同源请求（模块顶部声明的降级设计）。异常若照原样穿出，service 会
			# 把确定性失败记成可 resume 的 stopped，resume 永远重复同一次失败
			# （Issue #2428）。
			self._listener_ready = False
			return
		self._listener_ready = True

	def _joblist_packet_body(self, packet: Any) -> Any:
		"""解析监听包，并在按页码筛选之前执行统一的风险检查。"""
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
		return body

	def _wait_for_joblist_packet(self, target_page: int, *, query: str, city_code: str) -> Any | None:
		"""按关键词、城市和页码匹配监听结果，首屏允许省略页码。"""
		if self._page is None or not self._listener_ready:
			return None
		deadline = time.monotonic() + self._timeout
		while True:
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				return None
			packet = self._page.listen.wait(timeout=remaining, raise_err=False)
			if not packet:
				return None
			# 早期页的风险响应也必须停止，限流响应也必须交给调用方处理。
			body = self._joblist_packet_body(packet)
			response = getattr(packet, "response", None)
			if getattr(response, "status", None) == 429 or isinstance(body, dict) and body.get("code") in (9, "9"):
				return packet
			params = _joblist_query_params(packet)
			if params.get("query") != [query] or params.get("city") != [city_code]:
				continue
			page_no = _page_no_from_params(params) if "page" in params else 1
			if page_no == target_page:
				return packet

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
			except (ValueError, RecursionError):
				# 超长整数 / 深嵌套的裸 ValueError 也要按「非 JSON 文本」处理，
				# 否则风险页文本判定不会执行（Issue #1936）。
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
		# 顺序翻页时外层 attempt 认领的就是本次第一页的滚动请求（见下方循环），
		# 只有之后的翻页再各计一次：块内不会再发导航请求，外层不能额外多算一次
		# 预算（Issue #1982、#2094）。外层 attempt 仍覆盖响应等待与风控检查，
		# 风险锁在共享租约释放前落盘。
		first_turn_covered = not navigating
		try:
			with self._attempt("list-navigation" if navigating else "list-pagination"):
				try:
					position = self._page_no
					if position is None or target_page <= position:
						self._page.get(f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}")
						position = 1
					while position < target_page:
						# 每次翻页都会触发一次真实的 joblist 请求：预算与共享租约
						# 必须按次计入，否则从断点恢复到第 N 页时整段滚动只算 1 次
						# 请求，max_requests 与跨进程节流被绕过（Issue #1982）。
						if first_turn_covered:
							first_turn_covered = False
							self._trigger_next_page()
						else:
							with self._attempt("list-pagination"):
								self._trigger_next_page()
						position += 1
				except BaseException:
					# 导航/滚动可能已让页签前进但没有完成：位置不可信，下次重新导航。
					self._page_no = None
					raise
				self._page_no = position
				self._raise_if_security_page()
				packet = self._wait_for_joblist_packet(target_page, query=query, city_code=city_code)
				if packet is not None:
					response = getattr(packet, "response", None)
					body = self._joblist_packet_body(packet)
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
					listen_error = (
						TimeoutError(f"第 {page_no} 页等待 joblist.json 超时")
						if self._listener_ready
						else RuntimeError("职位监听器不可用")
					)
		except CrawlRiskError:
			raise
		except Exception as exc:
			if is_request_stop(exc) or _is_local_cache_error(exc):
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
			if is_request_stop(page_exc) or _is_local_cache_error(page_exc):
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
			except (ValueError, RecursionError) as exc:
				raise RuntimeError("页面内请求返回无法解析的传输结果") from exc
		if not isinstance(raw, dict):
			raise RuntimeError(f"页面内请求返回类型异常：{type(raw).__name__}（async JS 未等待完成时常见为 None）")
		status = int(raw.get("status") or 0)
		text = str(raw.get("text") or "")
		try:
			payload = json.loads(text)
		except (ValueError, RecursionError):
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
			except (ValueError, RecursionError):
				return None
			return parsed if isinstance(parsed, dict) else None
		if isinstance(value, dict):
			return value
		return None

	def fetch_detail(self, security_id: str) -> dict[str, Any]:
		"""在采集页签里取 job_card（同源请求，不把浏览器 Cookie / stoken 拷给 httpx）。"""
		if not security_id:
			# 空 ID 无法构造详情请求；伪造成功会让缺字段的条目永久标记完成
			# （Issue #1584）。调用方应保留 pending 状态。
			raise ValueError("security_id 不能为空，无法抓取职位详情")
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

	@staticmethod
	def _failure_reason(error: BaseException, *, secrets: Iterable[str] = ()) -> str:
		"""异常文本只用于本地诊断：先抹掉 Cookie 原值，再压成单行并截断。

		DrissionPage 的注入异常会把整个 cookie 字典（含 ``value``）拼进消息，
		而这段文本最终会进入 ``AUTH_REQUIRED`` 信封的 ``error.message``。
		输出层的按键脱敏拦不住第三方异常里的 ``'value'`` 键，必须在源头做
		值级替换（Issue #2168）；``SessionSeedResult`` 不保留任何原值。
		"""
		text = str(error)
		for secret in secrets:
			if secret:
				text = text.replace(secret, "[REDACTED]")
		text = text.replace("\r", " ").replace("\n", " ").strip()
		if len(text) > 200:
			text = text[:200] + "…"
		return f"{type(error).__name__}: {text}" if text else type(error).__name__

	def _seed_session_cookies(self) -> SessionSeedResult:
		"""Inject boss-login cookies into the isolated crawl profile.

		返回注入成功数与失败明细；调用方必须在进入业务页之前消费这个结果
		（Issue #1405）。seed 导航异常只放过 stop 信号，其余记录为失败原因而
		不再静默吞掉；真正的安全页仍由 ``_raise_if_platform_risk_page`` 抛
		``CrawlRiskError``，风控硬边界不变。
		"""
		if self._page is None or not self._seed_cookies:
			return SessionSeedResult()
		# Ensure domain context before setting cookies.
		navigation_error = ""
		try:
			with self._attempt("seed-navigation"):
				self._page.get("https://www.zhipin.com/")
		except CrawlRiskError:
			raise
		except Exception as error:
			if is_request_stop(error):
				raise
			navigation_error = self._failure_reason(error, secrets=self._seed_cookies.values())
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
			return SessionSeedResult(navigation_error=navigation_error)
		attempted = len(cookie_items)
		set_cookies = getattr(getattr(self._page, "set", None), "cookies", None)
		if not callable(set_cookies):
			return SessionSeedResult(
				attempted=attempted,
				navigation_error=navigation_error,
				failures=("当前 DrissionPage 版本不提供 set.cookies",),
			)
		try:
			set_cookies(cookie_items)
			return SessionSeedResult(attempted=attempted, injected=attempted, navigation_error=navigation_error)
		except Exception:
			# Fallback: one-by-one for older DrissionPage APIs.
			pass
		injected = 0
		failures: list[str] = []
		for item in cookie_items:
			try:
				set_cookies(item)
				injected += 1
			except Exception as error:
				failures.append(
					f"{item['name']}: {self._failure_reason(error, secrets=self._seed_cookies.values())}"
				)
		return SessionSeedResult(
			attempted=attempted,
			injected=injected,
			failures=tuple(failures),
			navigation_error=navigation_error,
		)

	def _raise_if_seed_auth_failure(self, seed: SessionSeedResult) -> None:
		"""本地登录态没准备好时按认证前置条件停止，不得落入平台风控路径。

		只有「注入失败 / seed 导航失败」才进入这里；页面是真正的安全页时
		``_raise_if_platform_risk_page`` 已经抛 ``CrawlRiskError``。为了不把
		浏览器 / CDP 的临时故障误报成账号风险，这里还要求页面像登录页
		（或 seed 导航本身失败）才停止（Issue #1405）。
		"""
		if self._page is None or not seed.failed:
			return
		url = str(getattr(self._page, "url", "")).lower()
		html = str(getattr(self._page, "html", ""))[:4000].lower()
		if not seed.navigation_error and not self._looks_like_login(url, html):
			return
		details: list[str] = []
		if seed.navigation_error:
			details.append(f"seed 首页导航失败（{seed.navigation_error}）")
		if seed.missing:
			failure_text = "；".join(seed.failures) if seed.failures else "注入异常"
			details.append(f"Cookie 注入失败 {seed.missing}/{seed.attempted}（{failure_text}）")
		raise CrawlSessionAuthError(
			"采集浏览器无法注入 boss login 的本地 Cookie（未命中平台风控）："
			+ "；".join(details)
			+ "。请重新执行 boss login 并确认采集 Chrome 可正常启动后重试"
		)

	@contextmanager
	def _profile_open_lock(self) -> Iterator[None]:
		"""串行化同一配置目录的打开流程；已被另一个进程持有时按占用处理。"""
		lock_path = self._profile_path / ".crawl-open.lock"
		with exclusive_file_lock(lock_path) as acquired:
			if not acquired:
				raise CrawlProfileBusy(
					f"采集配置目录 {self._profile_path} 正被另一个进程打开；请等待该进程完成浏览器启动或附着后重试"
				)
			yield

	def _verify_browser_profile_ownership(self, *, attached: bool) -> None:
		"""确认 CDP 连接的浏览器进程使用的是本采集配置目录（Issue #2464）。

		调试端口被日常 Chrome 或别的工具占用时，DrissionPage 会直接附着到那个
		浏览器：boss login 的 Cookie 会被注入用户自己的浏览器，「保留窗口供人工
		核对」的关闭路径还会退出它。归属由配置目录 SingletonLock 里的 Chrome
		主进程 pid 与 CDP 报告的浏览器进程比对；Windows 没有该锁时退回读进程
		命令行的 ``--user-data-dir``。附着路径确认不了就拒绝，且不碰该浏览器。
		"""
		process_id = _browser_process_id(self._page)
		lock_pid = self._profile_lock_pid(self._profile_path)
		if process_id is not None and lock_pid == process_id:
			return
		cmdline_profile = _browser_cmdline_profile(process_id) if process_id is not None else None
		if cmdline_profile is not None and _same_profile_directory(cmdline_profile, self._profile_path):
			return
		if not attached and process_id is None:
			# 端口空闲时浏览器由本进程拉起：读不到进程号（测试替身、受限平台）
			# 无法进一步确认，保持既有启动行为。
			return
		self._page = None
		owner = f"进程 {process_id}" if process_id is not None else "进程号不可读"
		raise CrawlProfileBusy(
			f"调试端口 {self._cdp_port} 上的浏览器（{owner}）不是采集配置目录 "
			f"{self._profile_path} 的 Chrome；已停止本次采集且未注入登录 Cookie。"
			"该端口可能被日常 Chrome 或其他工具占用：请关闭占用者，"
			"或为采集改用其他调试端口后重试"
		)

	@staticmethod
	def _profile_lock_pid(profile_path: Path) -> int | None:
		"""读 SingletonLock 记录的 Chrome 主进程 pid，不判断该进程是否存活。"""
		try:
			target = os.readlink(profile_path / "SingletonLock")
		except (OSError, ValueError):
			return None
		host, _, pid_text = target.rpartition("-")
		if host != socket.gethostname():
			return None
		try:
			pid = int(pid_text)
		except ValueError:
			return None
		return pid if pid > 0 else None

	@staticmethod
	def _profile_owner(profile_path: Path) -> int | None:
		"""返回正在使用该配置目录的 Chrome pid；锁陈旧、跨主机或不可解析时返回 None。

		Chrome 的 SingletonLock 是指向 ``<hostname>-<pid>`` 的符号链接。端口按 run
		随机分配时两个 run 的端口互不相同，端口探测发现不了共享 profile 的另一个
		Chrome，只有锁里的 pid 能说明配置目录正在被使用（Issue #1266）。读不出
		或者格式不认识时按陈旧处理，保持异常退出后自动恢复的既有行为；非 POSIX
		平台没有可读的锁目标，同样走清理路径。
		"""
		pid = DrissionCrawlerSession._profile_lock_pid(profile_path)
		if pid is None:
			return None
		try:
			os.kill(pid, 0)
		except (ProcessLookupError, OverflowError):
			# 超大 pid 无法转成 C long：符号链接格式不认识，按陈旧锁处理
			# （Issue #1583），不能让确定性本地失败被记成可重试的 stopped。
			return None
		except PermissionError:
			return pid
		except OSError:
			return None
		return pid

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
			# DrissionPage 未命中时返回 NoneElement（__bool__ 为 False、__eq__(None)
			# 为 True），不是 None；`is None` 会让这项风险检查永远不触发（Issue #1276）。
			if not find(".job-list-box", timeout=0) and not find(".job-card-wrapper", timeout=0):
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
