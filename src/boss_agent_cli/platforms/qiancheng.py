"""前程无忧（51job）平台占位实现。

当前仅注册候选者侧平台身份与稳定错误包络，不接真实 51job 接口。
真实只读 search/detail MVP 需另行确认稳定入口、脱敏 fixture 与错误映射后再启用。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.platforms.base import Platform


_NOT_SUPPORTED_MESSAGE = "51job/前程无忧适配器仍处于 research backlog，当前版本暂不支持真实平台能力"


class QianchengPlatform(Platform):
	"""51job 候选者侧占位 adapter。

	该实现有意不委托底层 client，也不访问任何 51job 真实接口；所有必需能力
	均返回稳定 ``NOT_SUPPORTED`` 包络，供 CLI / schema / 上层自动化明确识别。
	"""

	name = "qiancheng"
	display_name = "前程无忧（51job）"
	base_url = "https://www.51job.com"

	def __init__(self, client: Any | None = None) -> None:
		# 占位 adapter 不需要也不应构造真实网络 client。
		super().__init__(client)

	@property
	def client(self) -> Any | None:
		return self._client

	@staticmethod
	def _not_supported(capability: str) -> dict[str, Any]:
		message = f"{_NOT_SUPPORTED_MESSAGE}：{capability}"
		return {
			"code": -1,
			"data": None,
			"error": {
				"code": "NOT_SUPPORTED",
				"message": message,
				"recoverable": True,
				"recovery_action": None,
				"details": {
					"platform": "qiancheng",
					"capability": capability,
				},
			},
		}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("data")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		error = response.get("error")
		if isinstance(error, dict) and error.get("code") == "NOT_SUPPORTED":
			return "NOT_SUPPORTED", str(error.get("message") or "")
		code = response.get("code")
		message = str(response.get("message") or response.get("msg") or "")
		if code == "NOT_SUPPORTED":
			return "NOT_SUPPORTED", message
		return self._classify_platform_error(response)

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		return self._not_supported("search_jobs")

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._not_supported("job_detail")

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._not_supported("recommend_jobs")

	def user_info(self) -> dict[str, Any]:
		return self._not_supported("user_info")


	def resume_baseinfo(self) -> dict[str, Any]:
		return self._not_supported("resume_baseinfo")

	def resume_expect(self) -> dict[str, Any]:
		return self._not_supported("resume_expect")

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		return self._not_supported("deliver_list")

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		return self._not_supported("job_card")

	def interview_data(self) -> dict[str, Any]:
		return self._not_supported("interview_data")

	def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		return self._not_supported("chat_history")

	def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict[str, Any]:
		return self._not_supported("friend_label")

	def exchange_contact(self, security_id: str, uid: str, friend_name: str, exchange_type: int = 1) -> dict[str, Any]:
		return self._not_supported("exchange_contact")

	def job_history(self, page: int = 1) -> dict[str, Any]:
		return self._not_supported("job_history")

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		return self._not_supported("greet")

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		return self._not_supported("apply")

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		return self._not_supported("friend_list")
