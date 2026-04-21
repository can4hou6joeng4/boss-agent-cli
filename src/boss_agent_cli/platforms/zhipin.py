"""BOSS 直聘平台 adapter。

把现有 ``BossClient`` 包装为 ``Platform`` 实现，零行为变化。
后续新平台（智联等）实现同一 Platform 接口，
命令层可以通过 ``get_platform(name)`` 无差别调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.api.endpoints import BASE_URL
from boss_agent_cli.platforms.base import Platform

if TYPE_CHECKING:
	from boss_agent_cli.api.client import BossClient


# BOSS 直聘错误码 → 统一错误码映射（对齐 CLAUDE.md 错误码枚举）
_ERROR_CODE_MAP: dict[int, str] = {
	9: "RATE_LIMITED",
	36: "ACCOUNT_RISK",
	37: "TOKEN_REFRESH_FAILED",
}


class BossPlatform(Platform):
	"""BOSS 直聘平台实现。"""

	name = "zhipin"
	display_name = "BOSS 直聘"
	base_url = BASE_URL

	def __init__(self, client: "BossClient") -> None:
		super().__init__(client)
		# 重新 bound 出类型化属性，下游 IDE 可以看到 BossClient 方法
		self._client: "BossClient" = client

	# ── 包络适配 ────────────────────────────────────────

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		code = response.get("code")
		message = str(response.get("message") or response.get("zpData") or "")
		unified = _ERROR_CODE_MAP.get(code, "UNKNOWN") if isinstance(code, int) else "UNKNOWN"
		return unified, message

	# ── P0 只读委托 ─────────────────────────────────────

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		return self._client.search_jobs(query, **filters)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._client.job_detail(job_id)

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._client.recommend_jobs(page)

	def user_info(self) -> dict[str, Any]:
		return self._client.user_info()

	# ── P1 写操作委托 ───────────────────────────────────

	def greet(self, security_id: str, job_id: str, message: str = "") -> dict[str, Any]:
		return self._client.greet(security_id, job_id, message)

	def apply(self, security_id: str, job_id: str, lid: str = "") -> dict[str, Any]:
		return self._client.apply(security_id, job_id, lid)

	# ── P2 沟通委托 ─────────────────────────────────────

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		return self._client.friend_list(page)

	# ── P0+ 扩展方法（BossClient 全部支持）───────────────

	def resume_baseinfo(self) -> dict[str, Any]:
		return self._client.resume_baseinfo()

	def resume_expect(self) -> dict[str, Any]:
		return self._client.resume_expect()

	def deliver_list(self, page: int = 1) -> dict[str, Any]:
		return self._client.deliver_list(page=page)

	def job_card(self, security_id: str, lid: str = "") -> dict[str, Any]:
		return self._client.job_card(security_id, lid)

	def interview_data(self) -> dict[str, Any]:
		return self._client.interview_data()

	def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		return self._client.chat_history(gid, security_id, page=page, count=count)

	def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict[str, Any]:
		return self._client.friend_label(friend_id, label_id, friend_source, remove=remove)

	def exchange_contact(self, security_id: str, uid: str, friend_name: str, exchange_type: int = 1) -> dict[str, Any]:
		return self._client.exchange_contact(security_id, uid, friend_name, exchange_type=exchange_type)
