"""BOSS 直聘招聘者平台 adapter。

把 ``BossRecruiterClient`` 包装为 ``RecruiterPlatform`` 实现，零行为变化。
后续新平台实现同一 RecruiterPlatform 接口，
命令层可以通过 ``get_recruiter_platform(name)`` 无差别调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.api.recruiter_endpoints import BASE_URL
from boss_agent_cli.platforms.recruiter_base import RecruiterPlatform

if TYPE_CHECKING:
	from boss_agent_cli.api.recruiter_client import BossRecruiterClient


# BOSS 直聘错误码 → 统一错误码映射
_ERROR_CODE_MAP: dict[int, str] = {
	9: "RATE_LIMITED",
	36: "ACCOUNT_RISK",
	37: "TOKEN_REFRESH_FAILED",
	121: "INVALID_PARAM",
}

# 端点路径片段 → 已知端点漂移场景（命中后将该路径下的 121 重映射为 ENDPOINT_DEPRECATED）
# 真源：issue #217 — qianjunye 抓包确认 fastReply/sendReplyMsg 已被 BOSS 替换为 WS+Protobuf 双通道。
_DEPRECATED_ENDPOINT_FRAGMENTS: tuple[str, ...] = ("fastReply/sendReplyMsg",)


class BossRecruiterPlatform(RecruiterPlatform):
	"""BOSS 直聘招聘者平台实现。"""

	name = "zhipin-recruiter"
	display_name = "BOSS 直聘（招聘者）"
	base_url = BASE_URL

	def __init__(self, client: "BossRecruiterClient") -> None:
		super().__init__(client)
		self._client: "BossRecruiterClient" = client

	# ── 包络适配 ────────────────────────────────────────

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		code = response.get("code")
		message = str(response.get("message") or response.get("zpData") or "")
		unified = _ERROR_CODE_MAP.get(code, "UNKNOWN") if isinstance(code, int) else "UNKNOWN"
		# 端点漂移场景下重映射 121：调用方（_browser_request / _request）在 response dict 注入
		# __cli_endpoint_hint__ 字段（CLI 内部命名空间，避免与服务端字段冲突）。
		if code == 121:
			hint = response.get("__cli_endpoint_hint__")
			if isinstance(hint, str) and any(frag in hint for frag in _DEPRECATED_ENDPOINT_FRAGMENTS):
				unified = "ENDPOINT_DEPRECATED"
		return unified, message

	# ── 候选人列表与筛选 ────────────────────────────────

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict[str, Any]:
		return self._client.friend_list(page=page, label_id=label_id, job_id=job_id)

	def friend_detail(self, friend_ids: list[int]) -> dict[str, Any]:
		return self._client.friend_detail(friend_ids)

	def friend_labels(self) -> dict[str, Any]:
		return self._client.friend_labels()

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		return self._client.greet_list(page=page, job_id=job_id)

	def greet_rec_list(self, page: int = 1, job_id: str | None = None) -> dict[str, Any]:
		return self._client.greet_rec_list(page=page, job_id=job_id)

	# ── 候选人搜索与简历 ────────────────────────────────

	def search_geeks(
		self,
		query: str,
		*,
		city: str | None = None,
		page: int = 1,
		job_id: str | None = None,
		experience: str | None = None,
		degree: str | None = None,
		age: str | None = None,
		school_level: str | None = None,
		activeness: str | None = None,
		source: str | None = None,
		select: bool = False,
		salary: str | None = None,
	) -> dict[str, Any]:
		return self._client.search_geeks(
			query,
			city=city,
			page=page,
			job_id=job_id,
			experience=experience,
			degree=degree,
			age=age,
			school_level=school_level,
			activeness=activeness,
			source=source,
			select=select,
			salary=salary,
		)

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None = None) -> dict[str, Any]:
		return self._client.view_geek(geek_id, job_id=job_id, security_id=security_id)

	def chat_geek_info(self, geek_id: str, security_id: str, job_id: int) -> dict[str, Any]:
		return self._client.chat_geek_info(geek_id, security_id, job_id)

	# ── 消息 / 聊天 ──────────────────────────────────────

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		return self._client.last_messages(friend_ids)

	def chat_history(self, gid: int, *, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		return self._client.chat_history(gid, count=count, max_msg_id=max_msg_id)

	def send_message(self, gid: int, content: str) -> dict[str, Any]:
		return self._client.send_message(gid, content)

	def send_message_by_friend(self, friend_id: int, content: str) -> dict[str, Any]:
		return self._client.send_message_by_friend(friend_id, content)

	def session_enter(self, geek_id: str, expect_id: str, job_id: str, security_id: str) -> dict[str, Any]:
		return self._client.session_enter(geek_id, expect_id, job_id, security_id)

	# ── 职位管理 ──────────────────────────────────────────

	def list_jobs(self) -> dict[str, Any]:
		return self._client.list_jobs()

	def job_offline(self, job_id: str) -> dict[str, Any]:
		return self._client.job_offline(job_id)

	def job_online(self, job_id: str) -> dict[str, Any]:
		return self._client.job_online(job_id)

	def job_detail(self, enc_job_id: str) -> dict[str, Any]:
		return self._client.job_detail(enc_job_id)

	# ── 交换联系方式 ──────────────────────────────────────

	def exchange_request(self, exchange_type: int, uid: int, job_id: int, gid: int) -> dict[str, Any]:
		return self._client.exchange_request(exchange_type, uid, job_id, gid)

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict[str, Any]:
		return self._client.exchange_request_by_friend(friend_id, exchange_type)

	def exchange_content(self, uid: int) -> dict[str, Any]:
		return self._client.exchange_content(uid)

	# ── 面试 ──────────────────────────────────────────────

	def interview_list(self) -> dict[str, Any]:
		return self._client.interview_list()

	def interview_invite(self, geek_id: str, job_id: str, security_id: str, **kwargs: Any) -> dict[str, Any]:
		return self._client.interview_invite(geek_id, job_id, security_id, **kwargs)

	# ── 候选人操作 ────────────────────────────────────────

	def mark_unsuitable(self, geek_id: str, job_id: str) -> dict[str, Any]:
		return self._client.mark_unsuitable(geek_id, job_id)
