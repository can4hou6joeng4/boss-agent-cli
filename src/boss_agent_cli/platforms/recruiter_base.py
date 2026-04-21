"""招聘者平台抽象基类。

RecruiterPlatform 接口定义跨平台招聘者侧统一契约
（查看求职者 / 管理职位 / 沟通 等），
让 CLI 命令层通过 RecruiterPlatform 抽象调用，不耦合具体平台协议。

Week 1 骨架：接口定义 + BOSS 直聘招聘者 adapter。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any


class RecruiterPlatform(ABC):
	"""招聘者平台抽象基类。

	每个平台实现需覆盖：
	- 基础元信息（name / display_name / base_url）
	- 包络适配方法（is_success / unwrap_data / parse_error）
	- 求职者管理（list_applications / get_resume）
	- 职位管理（list_jobs / job_detail）

	写操作（request_resume / create_job / update_job / close_job）
	和沟通接口（friend_list / chat_history / send_message）为可选，
	平台不支持时抛 NotImplementedError。

	资源管理：支持 ``with`` 上下文管理器语法，``__exit__`` 自动调用 ``close()``
	释放底层 client 持有的 httpx / 浏览器资源。
	"""

	name: str
	display_name: str
	base_url: str

	def __init__(self, client: Any) -> None:
		"""ABC 构造签名：所有实现都接收一个平台专用 client。

		具体实现可以覆盖参数类型（如 ``BossRecruiterPlatform`` 声明 ``BossClient``）。
		"""
		self._client: Any = client

	# ── 资源生命周期 ───────────────────────────────────

	def close(self) -> None:
		"""释放底层资源。默认委托给 ``client.close()``（若存在）。"""
		close_fn = getattr(self._client, "close", None)
		if callable(close_fn):
			close_fn()

	def __enter__(self) -> "RecruiterPlatform":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()

	# ── 包络适配 ────────────────────────────────────────

	@abstractmethod
	def is_success(self, response: dict[str, Any]) -> bool:
		"""判断响应是否成功。"""

	@abstractmethod
	def unwrap_data(self, response: dict[str, Any]) -> Any:
		"""从响应包络提取 data。"""

	@abstractmethod
	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		"""解析错误响应，返回 (统一错误码, 原始消息)。"""

	# ── P0 求职者管理 ─────────────────────────────────

	@abstractmethod
	def list_applications(self, **filters: Any) -> dict[str, Any]:
		"""查看求职者 / 推荐牛人列表。"""

	@abstractmethod
	def get_resume(self, geek_id: str, security_id: str) -> dict[str, Any]:
		"""查看求职者简历。"""

	# ── P1 职位管理 ───────────────────────────────────

	@abstractmethod
	def list_jobs(self, page: int = 1) -> dict[str, Any]:
		"""查看职位列表。"""

	@abstractmethod
	def job_detail(self, job_id: str) -> dict[str, Any]:
		"""查看职位详情。"""

	# ── P2 沟通 ────────────────────────────────────────

	@abstractmethod
	def friend_list(self, page: int = 1) -> dict[str, Any]:
		"""沟通列表。"""

	# ── 可选写操作（默认抛 NotImplementedError）──────

	def request_resume(self, geek_id: str) -> dict[str, Any]:
		"""请求查看求职者简历。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement request_resume")

	def create_job(self, **params: Any) -> dict[str, Any]:
		"""创建职位。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement create_job")

	def update_job(self, job_id: str, **params: Any) -> dict[str, Any]:
		"""更新职位。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement update_job")

	def close_job(self, job_id: str) -> dict[str, Any]:
		"""关闭职位。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement close_job")

	def chat_history(self, friend_id: str, page: int = 1) -> dict[str, Any]:
		"""聊天历史。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement chat_history")

	def send_message(self, friend_id: str, message: str) -> dict[str, Any]:
		"""发送消息。平台不支持时抛 NotImplementedError。"""
		raise NotImplementedError(f"{self.name} platform does not implement send_message")
