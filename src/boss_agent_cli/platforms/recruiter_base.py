"""招聘者平台抽象基类。

RecruiterPlatform 接口定义跨平台招聘者侧统一契约
（候选人管理 / 职位管理 / 沟通 / 面试 等），
让 CLI 命令层通过 RecruiterPlatform 抽象调用，不耦合具体平台协议。
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
	- 候选人列表与筛选（friend_list / greet_list）
	- 候选人搜索与简历（search_geeks / view_geek）
	- 职位管理（list_jobs）
	- 消息（chat_history / send_message）

	可选写操作（job_offline / job_online / exchange_request /
	interview_list / interview_invite / mark_unsuitable）
	平台不支持时抛 NotImplementedError。

	资源管理：支持 ``with`` 上下文管理器语法，``__exit__`` 自动调用 ``close()``
	释放底层 client 持有的 httpx / 浏览器资源。
	"""

	name: str
	display_name: str
	base_url: str

	def __init__(self, client: Any) -> None:
		"""ABC 构造签名：所有实现都接收一个平台专用 client。"""
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

	# ── 候选人列表与筛选 ────────────────────────────────

	@abstractmethod
	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		"""沟通列表（按标签/职位筛选）。"""

	@abstractmethod
	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""打招呼列表。"""

	# ── 候选人搜索与简历 ────────────────────────────────

	@abstractmethod
	def search_geeks(self, query: str, *, city: str | None = None, page: int = 1, job_id: str | None = None, experience: str | None = None, degree: str | None = None) -> dict[str, Any]:
		"""搜索候选人。"""

	@abstractmethod
	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		"""查看候选人简历。"""

	# ── 消息 / 聊天 ──────────────────────────────────────

	@abstractmethod
	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		"""聊天历史记录。"""

	@abstractmethod
	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		"""发送消息。"""

	# ── 职位管理 ──────────────────────────────────────────

	@abstractmethod
	def list_jobs(self) -> dict[str, Any]:
		"""查看职位列表。"""

	# ── 可选操作（默认抛 NotImplementedError）──────

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		"""批量获取好友详情。"""
		raise NotImplementedError(f"{self.name} does not implement friend_detail")

	def friend_labels(self) -> dict[str, Any]:
		"""获取好友标签。"""
		raise NotImplementedError(f"{self.name} does not implement friend_labels")

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		"""推荐牛人招呼列表。"""
		raise NotImplementedError(f"{self.name} does not implement greet_rec_list")

	def chat_geek_info(self, geek_id: str, security_id: str, job_id: int) -> dict[str, Any]:
		"""获取候选人聊天信息。"""
		raise NotImplementedError(f"{self.name} does not implement chat_geek_info")

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		"""获取最近消息。"""
		raise NotImplementedError(f"{self.name} does not implement last_messages")

	def session_enter(self, geek_id: str, expect_id: str, job_id: str, security_id: str) -> dict[str, Any]:
		"""进入聊天会话。"""
		raise NotImplementedError(f"{self.name} does not implement session_enter")

	def job_offline(self, job_id: str) -> dict[str, Any]:
		"""下线职位。"""
		raise NotImplementedError(f"{self.name} does not implement job_offline")

	def job_online(self, job_id: str) -> dict[str, Any]:
		"""上线职位。"""
		raise NotImplementedError(f"{self.name} does not implement job_online")

	def exchange_request(self, exchange_type: int, uid: int, job_id: int, gid: int) -> dict[str, Any]:
		"""请求交换联系方式。"""
		raise NotImplementedError(f"{self.name} does not implement exchange_request")

	def exchange_content(self, uid: int) -> dict[str, Any]:
		"""获取交换内容。"""
		raise NotImplementedError(f"{self.name} does not implement exchange_content")

	def interview_list(self) -> dict[str, Any]:
		"""面试列表。"""
		raise NotImplementedError(f"{self.name} does not implement interview_list")

	def interview_invite(self, geek_id: str, job_id: str, security_id: str, **kwargs: Any) -> dict[str, Any]:
		"""邀请面试。"""
		raise NotImplementedError(f"{self.name} does not implement interview_invite")

	def mark_unsuitable(self, geek_id: str, job_id: str) -> dict[str, Any]:
		"""标记不合适。"""
		raise NotImplementedError(f"{self.name} does not implement mark_unsuitable")
