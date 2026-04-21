"""BOSS 直聘招聘者平台 adapter。

把现有 ``BossClient`` 包装为 ``RecruiterPlatform`` 实现，零行为变化。
后续新平台实现同一 RecruiterPlatform 接口，
命令层可以通过 ``get_recruiter_platform(name)`` 无差别调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.api.recruiter_endpoints import BASE_URL
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
	from boss_agent_cli.api.client import BossClient


# BOSS 直聘错误码 → 统一错误码映射（对齐 CLAUDE.md 错误码枚举）
_ERROR_CODE_MAP: dict[int, str] = {
	9: "RATE_LIMITED",
	36: "ACCOUNT_RISK",
	37: "TOKEN_REFRESH_FAILED",
}


class BossRecruiterPlatform(RecruiterPlatform):
	"""BOSS 直聘招聘者平台实现。"""

	name = "zhipin-recruiter"
	display_name = "BOSS 直聘（招聘者）"
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

	# ── P0 求职者管理 ─────────────────────────────────

	def list_applications(self, **filters: Any) -> dict[str, Any]:
		return self._client.list_applications(**filters)

	def get_resume(self, geek_id: str, security_id: str) -> dict[str, Any]:
		return self._client.get_resume(geek_id, security_id)

	# ── P1 职位管理 ───────────────────────────────────

	def list_jobs(self, page: int = 1) -> dict[str, Any]:
		return self._client.list_jobs(page)

	def job_detail(self, job_id: str) -> dict[str, Any]:
		return self._client.job_detail(job_id)

	# ── P2 沟通 ────────────────────────────────────────

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		return self._client.friend_list(page)
