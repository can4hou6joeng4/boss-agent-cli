#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[招聘平台抽象基类] 定义跨平台统一契约

Platform 接口设计理念：
- 定义 search/detail/greet/apply 等统一方法签名
- 让 CLI 命令层通过 Platform 抽象调用，不耦合具体平台协议
- 支持多平台扩展（BOSS直聘、智联、前程无忧）

Week 1 交付：接口定义 + BOSS 直聘 adapter，不改动现有命令行为。
详见 Issue #129。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any


class Platform(ABC):
	"""
	[招聘平台抽象基类]
	
	每个平台实现需覆盖：
	- 基础元信息（name / display_name / base_url）
	- 包络适配方法（is_success / unwrap_data / parse_error）
	- P0 只读能力（search / detail / recommend / user_info）
	
	写操作（greet / apply）和沟通接口（friend_list / chat_messages）为可选，
	平台不支持时抛 NotImplementedError。
	
	资源管理：支持 ``with`` 上下文管理器语法，``__exit__`` 自动调用 ``close()``
	释放底层 client 持有的 httpx / 浏览器资源。
	"""

	name: str  # 平台内部名称（如 'zhipin', 'zhilian'）
	display_name: str  # 平台展示名称
	base_url: str  # 平台主页 URL

	def __init__(self, client: Any) -> None:
		"""
		[ABC 构造签名] 所有实现都接收一个平台专用 client。
		
		具体实现可以覆盖参数类型（如 ``BossPlatform`` 声明 ``BossClient``）。
		"""
		self._client: Any = client

	# ── 资源生命周期管理 ───────────────────────────────────────────────

	def close(self) -> None:
		"""
		[释放底层资源] 默认委托给 ``client.close()``（若存在）。]
		
		子类可以覆盖以释放特定资源（如关闭 httpx.Client、浏览器会话等）。
		"""
		close_fn = getattr(self._client, "close", None)
		if callable(close_fn):
			close_fn()

	def __enter__(self) -> "Platform":
		"""
		[上下文管理器入口] 进入 with 块时返回自身。
		"""
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		"""
		[上下文管理器出口] 退出 with 块时调用 close() 释放资源。
		"""
		self.close()

	# ── 平台错误分类与统一处理 ───────────────────────────────────────────

	def _classify_platform_error(
		self,
		response: dict[str, Any],
		code_map: dict[int, str] | None = None,
		*,
		default: str = "UNKNOWN",
	) -> tuple[str, str]:
		"""
		[把平台错误包络统一分类为 CLI 稳定错误码]
		
		平台 adapter 可先传入各自的服务端错误码映射；未命中时按通用
		HTTP 状态码与消息关键词兜底，避免 401/429/网络错误等跨平台
		常见失败都退化为 UNKNOWN。
		
		返回值：(error_code, error_message)
		"""
		# 提取响应中的 code 和 message
		code = response.get("code")
		status_code = response.get("status_code") or response.get("status")
		message = str(
			response.get("message")
			or response.get("msg")
			or response.get("error")
			or response.get("zpData")
			or ""
		)
		
		# 优先使用平台特定的错误码映射
		mapping = code_map or {}
		if isinstance(code, int) and code in mapping:
			return mapping[code], message

		# 通用 HTTP 状态码分类
		numeric_code = code if isinstance(code, int) else status_code
		if numeric_code in (401, 40301):
			return "AUTH_EXPIRED", message  # 认证过期
		if numeric_code == 403:
			return "ACCOUNT_RISK", message  # 账户风险拦截
		if numeric_code == 429:
			return "RATE_LIMITED", message  # 请求限流
		if isinstance(numeric_code, int) and 500 <= numeric_code < 600:
			return "NETWORK_ERROR", message  # 服务器错误

		# 通过消息关键词分类
		lower_msg = message.lower()
		if any(token in lower_msg for token in ("stoken", "token", "unauthorized", "登录", "登陆", "未认证")):
			return "AUTH_EXPIRED", message
		if any(token in lower_msg for token in ("blocked", "forbidden", "禁止访问", "风控")):
			return "ACCOUNT_RISK", message
		if any(token in lower_msg for token in ("too many", "frequent", "频繁", "太快")):
			return "RATE_LIMITED", message
		if any(token in lower_msg for token in ("timeout", "网络", "连接", "network")):
			return "NETWORK_ERROR", message

		# 默认返回 UNKNOWN 错误码
		return default, message

	# ── 抽象方法：包络处理（每个平台必须实现）───────────────────────────

	@abstractmethod
	def is_success(self, response: dict[str, Any]) -> bool:
		"""
		[判断响应是否成功] 判断平台返回的响应是否表示成功。
		
		不同平台的成功判断逻辑不同：
		- BOSS直聘：code == 0
		- 智联：code == "1"
		"""
		...

	@abstractmethod
	def unwrap_data(self, response: dict[str, Any]) -> Any:
		"""
		[解包数据层] 从平台响应中解包出实际数据部分。
		
		不同平台的数据嵌套层级不同：
		- BOSS直聘：zpData
		- 智联：data
		"""
		...

	@abstractmethod
	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		"""
		[解析错误信息] 从失败响应中解析出错误码和错误消息。
		
		返回：(error_code, error_message)
		"""
		...

	# ── 抽象方法：P0 只读核心能力（每个平台必须实现）──────────────────

	@abstractmethod
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
		[搜索职位] 根据关键词搜索职位。
		
		参数：
		- keyword: 搜索关键词（如 'Python', 'Golang'）
		- city: 城市名（如 '北京'）
		- city_code: 城市代码（优先级高于 city 名）
		- salary: 薪资范围（如 '10-20K'）
		- experience: 经验要求
		- degree: 学历要求
		- page: 页码
		
		返回：平台原始响应（由 unwrap_data 解包）
		"""
		...

	@abstractmethod
	def get_job_detail(self, security_id: str, job_id: str) -> dict[str, Any]:
		"""
		[获取职位详情] 获取单个职位的详细信息。
		
		参数：
		- security_id: 职位安全 ID（BOSS直聘特有）
		- job_id: 职位 ID
		
		返回：职位详细信息
		"""
		...

	@abstractmethod
	def get_user_info(self) -> dict[str, Any]:
		"""
		[获取当前用户信息] 获取当前登录用户的个人信息。
		
		返回：用户信息
		"""
		...

	@abstractmethod
	def get_recommendations(self, **kwargs: Any) -> dict[str, Any]:
		"""
		[获取推荐职位] 获取平台为当前用户推荐的职位列表。
		
		返回：推荐职位列表
		"""
		...

	# ── 可选方法：写操作与沟通接口（平台不支持时抛 NotImplementedError）──────────────────────────────────────

	def greet(self, security_id: str, job_id: str, **kwargs: Any) -> dict[str, Any]:
		"""
		[打招呼] 向招聘者打招呼（可选实现）。
		
		注意：在默认低风险模式下，此操作会被合规护栏阻断。
		"""
		raise NotImplementedError(f"greet not supported on {self.name}")

	def apply(self, security_id: str, job_id: str, **kwargs: Any) -> dict[str, Any]:
		"""
		[投递简历] 投递简历给招聘者（可选实现）。
		
		注意：在默认低风险模式下，此操作会被合规护栏阻断。
		"""
		raise NotImplementedError(f"apply not supported on {self.name}")

	def get_friend_list(self, **kwargs: Any) -> dict[str, Any]:
		"""
		[获取好友列表] 获取与当前用户沟通过的招聘者列表（可选实现）。
		
		注意：在默认低风险模式下，此操作会被合规护栏阻断。
		"""
		raise NotImplementedError(f"get_friend_list not supported on {self.name}")

	def get_chat_messages(self, friend_id: str, **kwargs: Any) -> dict[str, Any]:
		"""
		[获取聊天记录] 获取与某个招聘者的聊天记录（可选实现）。
		
		注意：在默认低风险模式下，此操作会被合规护栏阻断。
		"""
		raise NotImplementedError(f"get_chat_messages not supported on {self.name}")
