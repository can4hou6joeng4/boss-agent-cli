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
		self._client = client

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
