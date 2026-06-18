"""智联招聘平台实现。

Week 2 P0 先接通只读能力，命令层通过 ``Platform`` 抽象即可无差别调用
``search_jobs`` / ``job_detail`` / ``recommend_jobs`` / ``user_info``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.platforms.base import Platform

if TYPE_CHECKING:
	from boss_agent_cli.api.zhilian_client import ZhilianClient


# 智联错误码 → 统一错误码映射（对齐 CLAUDE.md 错误码枚举）
_ERROR_CODE_MAP: dict[int, str] = {
	401: "AUTH_EXPIRED",
	403: "ACCOUNT_RISK",
	429: "RATE_LIMITED",
}

class ZhilianPlatform(Platform):
	"""智联招聘平台实现。"""

	name = "zhilian"
	display_name = "智联招聘"
	base_url = "https://m.zhaopin.com"

	def __init__(self, client: "ZhilianClient") -> None:
		super().__init__(client)
		self._client: "ZhilianClient" = client

	# ── 包络适配（Week 1d 已按 zhaopin.md 调研完成）──

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 200

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("data")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return self._classify_platform_error(response, _ERROR_CODE_MAP)

	# ── P0 只读委托 ────────────────────────────────

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		return self._client.search_jobs(query, **filters)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._client.job_detail(job_id)

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._client.recommend_jobs(page)

	def user_info(self) -> dict[str, Any]:
		return self._client.user_info()

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		return self._client.greet(security_id, job_id, message)

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		return self._client.apply(security_id, job_id, lid)

	def resume_baseinfo(self) -> dict[str, Any]:
		return self._client.resume_baseinfo()

	def resume_expect(self) -> dict[str, Any]:
		return self._client.resume_expect()

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		return self._client.deliver_list(page=page)

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		return self._client.job_card(security_id, lid)

	def interview_data(self) -> dict[str, Any]:
		"""智联面试邀请列表。透传 client 包络，由命令层 unwrap_data 解包。"""
		return self._client.interview_data()

	def job_history(self, page: int = 1) -> dict[str, Any]:
		return self._client.job_history(page)
