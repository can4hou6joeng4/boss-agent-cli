"""招聘平台抽象基类。

Platform 接口定义跨平台统一契约（search / detail / greet / apply 等），
让 CLI 命令层通过 Platform 抽象调用，不耦合具体平台协议。

Week 1 交付：接口定义 + BOSS 直聘 adapter，不改动现有命令行为。
详见 Issue #129。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Platform(ABC):
	"""招聘平台抽象基类。

	每个平台实现需覆盖：
	- 基础元信息（name / display_name / base_url）
	- 包络适配方法（is_success / unwrap_data / parse_error）
	- P0 只读能力（search / detail / recommend / user_info）

	写操作（greet / apply）和沟通接口（friend_list / chat_messages）为可选，
	平台不支持时抛 NotImplementedError。
	"""

	name: str
	display_name: str
	base_url: str

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

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		"""打招呼。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement greet")

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		"""发起投递。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement apply")

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		"""沟通列表。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement friend_list")
