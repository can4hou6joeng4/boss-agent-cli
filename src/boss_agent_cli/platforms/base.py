"""招聘平台抽象基类。

Platform 接口定义跨平台统一契约（search / detail / greet / apply 等），
让 CLI 命令层通过 Platform 抽象调用，不耦合具体平台协议。

Week 1 交付：接口定义 + BOSS 直聘 adapter，不改动现有命令行为。
详见 Issue #129。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any


class Platform(ABC):
	"""招聘平台抽象基类。

	每个平台实现需覆盖：
	- 基础元信息（name / display_name / base_url）
	- 包络适配方法（is_success / unwrap_data / parse_error）
	- P0 只读能力（search / detail / recommend / user_info）

	写操作（greet / apply）和沟通接口（friend_list / chat_messages）为可选，
	平台不支持时抛 NotImplementedError。

	资源管理：支持 ``with`` 上下文管理器语法，``__exit__`` 自动调用 ``close()``
	释放底层 client 持有的 httpx / 浏览器资源。
	"""

	name: str
	display_name: str
	base_url: str

	def __init__(self, client: Any) -> None:
		"""ABC 构造签名：所有实现都接收一个平台专用 client。

		具体实现可以覆盖参数类型（如 ``BossPlatform`` 声明 ``BossClient``）。
		"""
		self._client: Any = client

	# ── 资源生命周期 ───────────────────────────────────

	def close(self) -> None:
		"""释放底层资源。默认委托给 ``client.close()``（若存在）。"""
		close_fn = getattr(self._client, "close", None)
		if callable(close_fn):
			close_fn()

	def __enter__(self) -> "Platform":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()

	def _classify_platform_error(
		self,
		response: dict[str, Any],
		code_map: dict[int, str] | None = None,
		*,
		default: str = "UNKNOWN",
	) -> tuple[str, str]:
		"""把平台错误包络统一分类为 CLI 稳定错误码。

		平台 adapter 可先传入各自的服务端错误码映射；未命中时按通用
		HTTP 状态码与消息关键词兜底，避免 401/429/网络错误等跨平台
		常见失败都退化为 UNKNOWN。
		"""
		code = response.get("code")
		status_code = response.get("status_code") or response.get("status")
		message = str(
			response.get("message")
			or response.get("msg")
			or response.get("error")
			or response.get("zpData")
			or ""
		)
		mapping = code_map or {}
		if isinstance(code, int) and code in mapping:
			return mapping[code], message

		numeric_code = code if isinstance(code, int) else status_code
		if numeric_code in (401, 40301):
			return "AUTH_EXPIRED", message
		if numeric_code == 403:
			return "ACCOUNT_RISK", message
		if numeric_code == 429:
			return "RATE_LIMITED", message
		if isinstance(numeric_code, int) and 500 <= numeric_code < 600:
			return "NETWORK_ERROR", message

		lower_msg = message.lower()
		if any(token in lower_msg for token in ("stoken", "token", "unauthorized", "登录", "登陆", "未认证")):
			return "AUTH_EXPIRED", message
		if any(token in lower_msg for token in ("too many", "rate", "频繁", "限流", "稍后再试")):
			return "RATE_LIMITED", message
		if any(token in lower_msg for token in ("risk", "forbidden", "风控", "安全验证", "异常访问")):
			return "ACCOUNT_RISK", message
		if any(token in lower_msg for token in ("timeout", "timed out", "network", "connection", "网络", "连接")):
			return "NETWORK_ERROR", message

		return default, message

	@abstractmethod
	def is_success(self, response: dict[str, Any]) -> bool:
		"""判断响应是否成功。

		- BOSS: ``code == 0``
		- 智联: ``code == 200``
		- 猎聘: ``flag == 1``
		"""

	@abstractmethod
	def unwrap_data(self, response: dict[str, Any]) -> Any:
		"""从响应包络提取 data。

		- BOSS: ``response["zpData"]``
		- 智联: ``response["data"]``
		- 猎聘: ``response["data"]``
		"""

	@abstractmethod
	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		"""解析错误响应，返回 (统一错误码, 原始消息)。

		统一错误码对齐 CLAUDE.md 错误码枚举：
		AUTH_EXPIRED / RATE_LIMITED / TOKEN_REFRESH_FAILED / ACCOUNT_RISK / UNKNOWN。
		"""

	@abstractmethod
	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		"""搜索职位。"""

	@abstractmethod
	def job_detail(self, job_id: str) -> dict[str, Any]:
		"""查看职位详情。"""

	@abstractmethod
	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		"""获取个性化推荐。"""

	@abstractmethod
	def user_info(self) -> dict[str, Any]:
		"""获取当前用户信息。"""

	# ── 简历 / 投递（P0+，平台不支持时抛 NotImplementedError）──

	def resume_baseinfo(self) -> dict[str, Any]:
		"""获取简历基本信息。"""
		raise NotImplementedError(f"{self.name} platform does not implement resume_baseinfo")

	def resume_expect(self) -> dict[str, Any]:
		"""获取求职期望。"""
		raise NotImplementedError(f"{self.name} platform does not implement resume_expect")

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		"""获取投递记录。"""
		raise NotImplementedError(f"{self.name} platform does not implement deliver_list")

	# ── 职位卡片（详情降级通道）──

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		"""职位卡片（用于详情页降级）。"""
		raise NotImplementedError(f"{self.name} platform does not implement job_card")

	# ── 面试 ──

	def interview_data(self) -> dict[str, Any]:
		"""获取面试邀请列表。"""
		raise NotImplementedError(f"{self.name} platform does not implement interview_data")

	# ── 沟通扩展 ──

	def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		"""查看与某联系人的聊天消息历史。"""
		raise NotImplementedError(f"{self.name} platform does not implement chat_history")

	def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict[str, Any]:
		"""给联系人加/删标签。"""
		raise NotImplementedError(f"{self.name} platform does not implement friend_label")

	def exchange_contact(self, security_id: str, uid: str, friend_name: str, exchange_type: int = 1) -> dict[str, Any]:
		"""请求交换联系方式（手机 / 微信）。"""
		raise NotImplementedError(f"{self.name} platform does not implement exchange_contact")

	def job_history(self, page: int = 1) -> dict[str, Any]:
		"""浏览历史。"""
		raise NotImplementedError(f"{self.name} platform does not implement job_history")

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		"""打招呼。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement greet")

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		"""发起投递。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement apply")

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		"""沟通列表。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement friend_list")
