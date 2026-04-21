"""智联招聘平台 stub 实现。

**当前状态：Week 1d 自证 stub** — 包络适配方法按 zhaopin.md 调研给出真实逻辑，
P0/P1/P2 抽象方法暂抛 `NotImplementedError("Week 2 待实现")`，
让 Platform 抽象在两个平台上都可编译 / 注册 / schema 可见。

**Week 2 TODO**（Issue #129 Week 2）：
- 实现 ZhilianClient（基于 BrowserSession / Bridge 通道）
- 实现 search_jobs / job_detail / recommend_jobs / user_info 只读方法
- 基于调研 [docs/research/platforms/zhaopin.md](../../docs/research/platforms/zhaopin.md)

协议核心差异（对比 BOSS 直聘）：
- 成功码：`code == 200`（BOSS 是 `code == 0`）
- 数据包络：`response["data"]`（BOSS 是 `response["zpData"]`）
- 错误码：401 未授权 / 403 风控 / 429 限流（BOSS 用 code 9/36/37）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boss_agent_cli.platforms.base import Platform

if TYPE_CHECKING:
	# Week 2 引入 ZhilianClient；当前 stub 无依赖
	pass


# 智联错误码 → 统一错误码映射（对齐 CLAUDE.md 错误码枚举）
_ERROR_CODE_MAP: dict[int, str] = {
	401: "AUTH_EXPIRED",
	403: "ACCOUNT_RISK",
	429: "RATE_LIMITED",
}

# Week 2 时会替换为真实实现
_NOT_YET_MSG = "Zhilian Week 2 待实现，当前为注册自证 stub，追踪进度见 Issue #140（https://github.com/can4hou6joeng4/boss-agent-cli/issues/140）"


class ZhilianPlatform(Platform):
	"""智联招聘平台实现（当前为 Week 1d 自证 stub）。"""

	name = "zhilian"
	display_name = "智联招聘"
	base_url = "https://m.zhaopin.com"

	def __init__(self, client: Any) -> None:
		super().__init__(client)

	# ── 包络适配（Week 1d 已按 zhaopin.md 调研完成）──

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 200

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("data")

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		code = response.get("code")
		message = str(response.get("message") or "")
		unified = _ERROR_CODE_MAP.get(code, "UNKNOWN") if isinstance(code, int) else "UNKNOWN"
		return unified, message

	# ── P0 只读（Week 2 待实现）─────────────────────

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: search_jobs(query={query!r}, filters={filters!r})")

	def job_detail(self, job_id: str) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: job_detail(job_id={job_id!r})")

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: recommend_jobs(page={page})")

	def user_info(self) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: user_info()")

	# greet / apply / friend_list 沿用 Platform 基类的 NotImplementedError 默认
