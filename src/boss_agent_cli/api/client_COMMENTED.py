#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[混合 API 客户端] BOSS 直聘 API 调用核心模块

设计理念：
- 低风险操作用 httpx（快速、轻量）
- 高风险操作用浏览器 CDP（更像真人，避免风控）
- 请求节流：高斯分布延迟，模拟真人操作节奏
- 自动重试：失败重试 3 次
- Cookie 自动合并：保持会话一致性
"""

import atexit
import random
import time
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

import httpx

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.httpx_helpers import (
	add_stoken_to_get_params,
	browser_headers,
	merge_response_cookies,
	referer_header,
)
from boss_agent_cli.api.throttle import RequestThrottle

if TYPE_CHECKING:
	from boss_agent_cli.api.browser_client import BrowserSession
	from boss_agent_cli.auth.manager import AuthManager

_MAX_RETRIES = 3  # 最大重试次数

# atexit 安全机制：程序退出时自动关闭未显式关闭的 BossClient 实例
_OPEN_CLIENTS: weakref.WeakSet["BossClient"] = weakref.WeakSet()


def _close_open_clients() -> None:
	"""
	[退出清理] atexit 回调：清理所有未关闭的客户端，防止资源泄漏。
	"""
	for client in list(_OPEN_CLIENTS):
		try:
			client.close()
		except Exception:
			pass


atexit.register(_close_open_clients)


class AuthError(Exception):
	"""
	[认证错误] 未登录或令牌过期时抛出。
	"""
	pass


class AccountRiskError(Exception):
	"""
	[账户风险错误] BOSS 直聘风控拦截（code 36）：检测到异常行为。
	
	属性：
	- is_cdp: 是否通过浏览器 CDP 操作被拦截
	"""
	def __init__(self, message: str = "", is_cdp: bool = False):
		self.is_cdp = is_cdp
		super().__init__(message)


class BossClient:
	"""
	[混合 API 客户端] 高风险操作用浏览器，低风险操作用 httpx。
	
	核心特性：
	1. 双通道：httpx（快）+ Browser CDP（稳）
	2. 请求节流：高斯分布延迟，避免被识别为机器人
	3. 自动重试：3次重试 + 退避策略
	4. Cookie 合并：保持会话一致性
	5. 上下文管理器支持：with BossClient() as client:
	"""

	def __init__(self, auth_manager: "AuthManager", *, delay: tuple[float, float] = (1.5, 3.0), cdp_url: str | None = None) -> None:
		"""
		[构造函数] 初始化 BossClient 实例。
		
		参数：
		- auth_manager: 认证管理器，提供令牌
		- delay: 请求延迟范围（秒），如 (1.5, 3.0)，高斯分布随机延迟
		- cdp_url: Chrome CDP 调试地址，如 http://localhost:9222
		"""
		self._auth = auth_manager
		self._delay = delay
		self._client: httpx.Client | None = None  # httpx 客户端（懒加载）
		self._browser_session: "BrowserSession | None" = None  # 浏览器会话（懒加载）
		self._throttle = RequestThrottle(delay)  # 请求节流器
		self._cdp_url = cdp_url
		self._closed = False
		_OPEN_CLIENTS.add(self)  # 注册到清理列表

	def _get_client(self) -> httpx.Client:
		"""
		[获取 httpx 客户端] 懒加载模式：第一次调用时才创建。
		
		每次调用会检查令牌是否过期，如果过期会刷新。
		"""
		if self._client is None:
			token = self._auth.get_token()
			headers = browser_headers(endpoints.DEFAULT_HEADERS, token)
			self._client = httpx.Client(
				base_url=endpoints.BASE_URL,
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._client

	def _get_browser(self) -> "BrowserSession":
		"""
		[获取浏览器会话] 懒加载模式：第一次调用时才创建。
		
		用于高风险操作（如投递、打招呼），更像真人，避免风控。
		"""
		if self._browser_session is None:
			from boss_agent_cli.api.browser_client import BrowserSession
			token = self._auth.get_token()
			self._browser_session = BrowserSession(
				cookies=token.get("cookies", {}),
				user_agent=token.get("user_agent", ""),
				delay=self._delay,
				cdp_url=self._cdp_url,
				logger=getattr(self._auth, '_logger', None),
			)
		return self._browser_session

	# ── Anti-detection 辅助（httpx 通道）──────────────────────────────

	def _headers_for(self, url: str) -> dict[str, str]:
		"""
		[生成 Referer Header] 根据目标 URL 生成合适的 Referer Header。
		
		Referer 用于模拟正常的浏览器跳转行为，减少被识别为机器人的风险。
		"""
		return referer_header(url, endpoints.REFERER_MAP, f"{endpoints.BASE_URL}/")

	def _merge_cookies(self, resp: httpx.Response) -> None:
		"""
		[合并响应 Cookie] 将响应的 Set-Cookie 合并到客户端 CookieJar。
		
		保持会话一致性，避免被踢下线。
		"""
		merge_response_cookies(self._get_client(), resp)

	# ── httpx 请求（低风险操作）────────────────────────────────────────

	def _request(
		self,
		method: str,
		path: str,
		*,
		params: dict[str, Any] | None = None,
		json: Any = None,
		data: Any = None,
		use_stoken: bool = True,
		**kwargs: Any,
	) -> httpx.Response:
		"""
		[通用 HTTP 请求] 封装 httpx 请求，添加节流、重试、Cookie 合并。
		
		参数：
		- method: HTTP 方法（GET/POST/...）
		- path: API 路径
		- params: URL 查询参数
		- json: JSON 请求体
		- data: Form 数据
		- use_stoken: 是否添加 stoken 参数（BOSS直聘安全令牌）
		
		返回：httpx.Response
		"""
		# [1] 获取客户端，确保 Cookie 和 Headers 最新
		client = self._get_client()
		
		# [2] 构建请求 URL 和参数
		# BOSS直聘有些 API 需要 stoken 参数来防 CSRF
		params = dict(params or {})
		if use_stoken and method == "GET":
			token = self._auth.get_token()
			params = add_stoken_to_get_params(params, token)
		
		# [3] 节流：等待一段时间，模拟真人操作
		self._throttle.wait()
		
		# [4] 构建请求参数
		url = f"{endpoints.BASE_URL}{path}"
		headers = self._headers_for(url)
		
		# [5] 执行请求（带重试机制）
		last_err: Exception | None = None
		for attempt in range(_MAX_RETRIES):
			try:
				resp = client.request(
					method=method,
					url=url,
					params=params,
					json=json,
					data=data,
					headers=headers,
					**kwargs,
				)
				
				# [6] 合并 Cookie
				self._merge_cookies(resp)
				
				# [7] 返回响应（即使失败也返回，让上层判断）
				return resp
			except Exception as e:
				last_err = e
				# 指数退避：第1次等1秒，第2次等2秒，第3次等4秒
				wait = 2 ** attempt
				time.sleep(wait)
		
		# [8] 重试次数用尽，抛出最后一个错误
		if last_err:
			raise last_err
		raise RuntimeError("request failed without error")

	# ── 浏览器请求（高风险操作）─────────────────────────────────────────

	def _browser_request(
		self,
		method: str,
		path: str,
		*,
		params: dict[str, Any] | None = None,
		json: Any = None,
		data: Any = None,
		**kwargs: Any,
	) -> dict[str, Any]:
		"""
		[浏览器请求] 通过 Chrome CDP 执行请求（用于高风险操作）。
		
		优势：
		- 更像真人操作（包括 JS 执行、渲染、cookie 更新）
		- 更难被风控识别
		
		劣势：
		- 比 httpx 慢
		- 需要启动浏览器
		"""
		browser = self._get_browser()
		url = f"{endpoints.BASE_URL}{path}"
		return browser.request(
			method=method,
			url=url,
			params=params,
			json=json,
			data=data,
			**kwargs,
		)

	# ── 核心 API 方法（搜索、详情等）────────────────────────────────────

	def search_jobs(
		self,
		keyword: str,
		*,
		city: str | None = None,
		city_code: str | None = None,
		salary: str | None = None,
		experience: str | None = None,
		degree: str | None = None,
		page: int = 1,
		**kwargs: Any,
	) -> dict[str, Any]:
		"""
		[搜索职位] 调用 BOSS 直聘搜索 API。
		
		参数：
		- keyword: 搜索关键词
		- city: 城市名（如 '北京'）
		- city_code: 城市代码（优先级更高）
		- salary: 薪资范围
		- experience: 经验要求
		- degree: 学历要求
		- page: 页码
		
		返回：API 原始响应
		"""
		path = endpoints.SEARCH_PATH
		params = dict(endpoints.make_search_params(
			keyword=keyword,
			city=city,
			city_code=city_code,
			salary=salary,
			experience=experience,
			degree=degree,
			page=page,
			**kwargs,
		))
		
		# 使用 httpx（搜索是低风险操作）
		resp = self._request("GET", path, params=params)
		return resp.json()

	def get_job_detail(self, security_id: str, job_id: str) -> dict[str, Any]:
		"""
		[获取职位详情] 获取单个职位的详细信息。
		
		参数：
		- security_id: BOSS直聘职位安全 ID（URL 中的 securityId 参数）
		- job_id: 职位 ID
		
		返回：职位详情
		"""
		path = endpoints.DETAIL_PATH
		params = endpoints.make_detail_params(security_id=security_id, job_id=job_id)
		
		# 使用 httpx（查看详情是低风险操作）
		resp = self._request("GET", path, params=params)
		return resp.json()

	# ── 资源清理 ────────────────────────────────────────────────────────

	def close(self) -> None:
		"""
		[关闭客户端] 释放所有资源（httpx 和浏览器）。
		"""
		if self._closed:
			return
		
		if self._client:
			self._client.close()
			self._client = None
		
		if self._browser_session:
			self._browser_session.close()
			self._browser_session = None
		
		self._closed = True
		try:
			_OPEN_CLIENTS.remove(self)
		except KeyError:
			pass

	def __enter__(self) -> "BossClient":
		"""
		[上下文管理器入口] 支持 with 语法。
		"""
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		"""
		[上下文管理器出口] 自动关闭客户端。
		"""
		self.close()
