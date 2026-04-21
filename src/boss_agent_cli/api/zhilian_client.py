"""智联招聘内部 HTTP 客户端骨架（Week 2 实施前占位）。

**当前状态**：类结构 + __init__/close 签名对齐 BossClient，所有 P0/P1/P2 方法抛
``NotImplementedError("Zhilian client Week 2 待实现，详见 Issue #140")``。

**Week 2 TODO**（Issue #140）：
- 基于 httpx 实现真实 HTTP 调用
- 基于 BrowserSession / Bridge 获取 CSRF token
- 实现 search_jobs / job_detail / recommend_jobs / user_info 四个 P0 只读方法
- 认证链路（Cookie + X-Lagou-Token 或智联对应 token）

**设计依据**：
- [docs/research/platforms/zhaopin.md](../../docs/research/platforms/zhaopin.md) §2 端点清单
- [src/boss_agent_cli/platforms/zhilian.py](../platforms/zhilian.py) Platform 适配层
"""

from __future__ import annotations

import atexit
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from boss_agent_cli.auth.manager import AuthManager


_NOT_YET_MSG = (
	"Zhilian client Week 2 待实现，当前为 stub 占位，"
	"追踪进度见 Issue #140（https://github.com/can4hou6joeng4/boss-agent-cli/issues/140）"
)


# atexit safeguard：类比 BossClient 的管理方式
_OPEN_CLIENTS: weakref.WeakSet["ZhilianClient"] = weakref.WeakSet()


def _close_open_clients() -> None:
	for client in list(_OPEN_CLIENTS):
		try:
			client.close()
		except Exception:
			pass


atexit.register(_close_open_clients)


class ZhilianClient:
	"""智联招聘内部 HTTP 客户端骨架。

	签名对齐 ``BossClient``，Week 2 填充真实实现。
	"""

	def __init__(
		self,
		auth_manager: "AuthManager",
		*,
		delay: tuple[float, float] = (1.5, 3.0),
		cdp_url: str | None = None,
	) -> None:
		self._auth = auth_manager
		self._delay = delay
		self._cdp_url = cdp_url
		self._closed = False
		_OPEN_CLIENTS.add(self)

	# ── 资源生命周期 ───────────────────────────────

	def close(self) -> None:
		"""释放底层资源。Week 2 真实现后会关闭 httpx client 和 browser session。"""
		self._closed = True

	def __enter__(self) -> "ZhilianClient":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()

	# ── P0 只读（Week 2 待实现）─────────────────────

	def search_jobs(self, query: str, **filters: Any) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: search_jobs(query={query!r}, filters={filters!r})")

	def job_detail(self, job_id: str) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: job_detail(job_id={job_id!r})")

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: recommend_jobs(page={page})")

	def user_info(self) -> dict[str, Any]:
		raise NotImplementedError(f"{_NOT_YET_MSG}: user_info()")
