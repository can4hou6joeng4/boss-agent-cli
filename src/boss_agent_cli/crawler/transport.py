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

JOBLIST_TARGET = r"wapi/zpgeek/search/joblist\.json"
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
	) -> None:
		self._profile_path = profile_path
		self._chrome_path = chrome_path
		self._cdp_port = cdp_port
		self._hook_profile = hook_profile
		self._hook_dir = hook_dir
		self._timeout = timeout
		self._page: Any = None
		self._details: httpx.Client | None = None
		self._detail_stoken = ""
		self._started = False

	def open(self) -> list[HookInjection]:
		try:
			from DrissionPage import ChromiumOptions, ChromiumPage
		except ImportError as exc:
			raise RuntimeError("crawl 需要 DrissionPage；请安装 boss-agent-cli[crawl]") from exc
		options = ChromiumOptions()
		if self._port_is_in_use(self._cdp_port):
			raise RuntimeError(f"CDP 端口 {self._cdp_port} 已被占用；crawl 只能启动自己的独立浏览器")
		self._profile_path = self._profile_path.resolve()
		self._profile_path.mkdir(parents=True, exist_ok=True)
		if self._chrome_path:
			options.set_browser_path(self._chrome_path)
		options.set_local_port(self._cdp_port)
		options.set_user_data_path(str(self._profile_path))
		self._page = ChromiumPage(options)
		results = inject_hook_profile(self._page, self._hook_profile, self._hook_dir)
		failed = [item for item in results if not item.success]
		if failed:
			raise HookRegistrationError(results)
		self._page.listen.start(JOBLIST_TARGET, is_regex=True, method=("GET", "POST"))
		return results

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict[str, Any]:
		if self._page is None:
			raise RuntimeError("Drission crawler session is not open")
		if self._started:
			self._trigger_next_page()
		else:
			self._page.get(f"https://www.zhipin.com/web/geek/jobs?city={city_code}&query={quote(query)}")
			self._started = True
		self._raise_if_security_page()
		packet = self._page.listen.wait(timeout=self._timeout, raise_err=False)
		if not packet:
			self._raise_if_security_page(require_job_list=True)
			raise TimeoutError(f"第 {page_no} 页等待 joblist.json 超时")
		body = packet.response.body
		if isinstance(body, str):
			try:
				body = json.loads(body)
			except json.JSONDecodeError as exc:
				self._raise_if_security_page(require_job_list=True)
				raise CrawlRiskError(f"第 {page_no} 页 joblist.json 返回非 JSON 内容，已停止以避免继续触发验证") from exc
		if not isinstance(body, dict):
			self._raise_if_security_page(require_job_list=True)
			raise CrawlRiskError(f"第 {page_no} 页 joblist.json 响应不是对象，已停止以避免继续触发验证")
		self._raise_if_risk_response(body)
		return body

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

	def close(self) -> None:
		if self._details is not None:
			self._details.close()
			self._details = None
		if self._page is not None:
			quit_browser = getattr(self._page, "quit", None)
			if callable(quit_browser):
				quit_browser()
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

	def _raise_if_security_page(self, *, require_job_list: bool = False) -> None:
		if self._page is None:
			return
		url = str(getattr(self._page, "url", "")).lower()
		html = str(getattr(self._page, "html", ""))[:4000].lower()
		if "zhipin-security" in url or "zhipin-security" in html:
			raise CrawlRiskError("检测到 zhipin-security 安全页")
		if not require_job_list:
			return
		find = getattr(self._page, "ele", None)
		if not callable(find):
			return
		try:
			if find(".job-list-box", timeout=0) is None and find(".job-card-wrapper", timeout=0) is None:
				raise CrawlRiskError("页面缺少职位列表容器，疑似安全页")
		except CrawlRiskError:
			raise
		except Exception:
			# 页面尚未支持元素探测时保留普通超时语义，不能把探测错误误判为风险。
			return

	@staticmethod
	def _raise_if_risk_response(payload: dict[str, Any]) -> None:
		code = payload.get("code")
		if code in (37, 38):
			raise CrawlRiskError(f"平台返回风险码 code={code}: {payload.get('message', '')}")
