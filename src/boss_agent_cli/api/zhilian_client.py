"""智联招聘内部 HTTP 客户端骨架（Week 2 实施前占位）。

**当前状态**：类结构 + __init__/close 签名对齐 BossClient，所有 P0/P1/P2 方法抛
``NotImplementedError("Zhilian client Week 2 待实现，详见 Issue #140")``。

**Week 2 TODO**（Issue #140）：
- 基于 httpx 实现真实 HTTP 调用
- 基于 BrowserSession / Bridge 获取 CSRF token
- 实现 search_jobs / job_detail / recommend_jobs / user_info 四个 P0 只读方法
- 认证链路（Cookie + X-Lagou-Token 或智联对应 token）

**P0-1 已实施**（本文件 `_get_httpx_client`）：
- httpx.Client lazy 初始化 + 资源生命周期闭环
- 注入 Cookie / User-Agent / sec-ch-ua-platform / x-zp-client-id / Referer / Origin
- 多域名（fe-api / i / www zhaopin.com）不绑死 base_url，调用时传完整 URL

**设计依据**：
- [docs/research/platforms/zhaopin.md](../../docs/research/platforms/zhaopin.md) §2 端点清单
- [src/boss_agent_cli/platforms/zhilian.py](../platforms/zhilian.py) Platform 适配层
"""

from __future__ import annotations

import atexit
import sys
import weakref
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
	from boss_agent_cli.auth.manager import AuthManager


_NOT_YET_MSG = (
	"Zhilian client Week 2 待实现，当前为 stub 占位，"
	"追踪进度见 Issue #140（https://github.com/can4hou6joeng4/boss-agent-cli/issues/140）"
)

# 智联站点常量 — 多域名不绑死 base_url，只用于 Referer / Origin
ZHAOPIN_HOME = "https://www.zhaopin.com"
ZHAOPIN_DOMAIN = "https://www.zhaopin.com/"

# 兜底 UA（真实浏览器指纹，token 未提供时用）
_DEFAULT_USER_AGENT = (
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
	"AppleWebKit/537.36 (KHTML, like Gecko) "
	"Chrome/123.0.0.0 Safari/537.36"
)

# sec-ch-ua-platform 按运行平台动态映射
_SEC_CH_UA_PLATFORM_MAP: dict[str, str] = {
	"win32": '"Windows"',
	"linux": '"Linux"',
	"darwin": '"macOS"',
}

# 默认 header 模板（不含 token / 平台动态字段）
_DEFAULT_HEADERS: dict[str, str] = {
	"Accept": "application/json, text/plain, */*",
	"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
	"sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
	"sec-ch-ua-mobile": "?0",
	"Sec-Fetch-Site": "same-site",
	"Sec-Fetch-Mode": "cors",
	"Sec-Fetch-Dest": "empty",
	"Referer": ZHAOPIN_DOMAIN,
	"Origin": ZHAOPIN_HOME,
}


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
		self._httpx_client: httpx.Client | None = None
		self._closed = False
		_OPEN_CLIENTS.add(self)

	# ── 资源生命周期 ───────────────────────────────

	def _get_httpx_client(self) -> httpx.Client:
		"""惰性初始化 httpx.Client，注入认证 Cookie 与浏览器指纹头。

		设计要点：
		- 不绑 ``base_url``：智联多域名（fe-api / i / www zhaopin.com），
		  调用方传完整 URL 即可。
		- token 字段优先：``user_agent`` / ``x_zp_client_id`` 由
		  ``auth_manager.get_token()`` 提供，缺失则用兜底 UA 与跳过 client_id。
		- ``sec-ch-ua-platform`` 按 ``sys.platform`` 动态映射，避免 headless 检测。
		"""
		if self._httpx_client is None:
			token = self._auth.get_token()
			headers = dict(_DEFAULT_HEADERS)

			ua = token.get("user_agent")
			headers["User-Agent"] = ua if ua else _DEFAULT_USER_AGENT

			headers["sec-ch-ua-platform"] = _SEC_CH_UA_PLATFORM_MAP.get(sys.platform, '"macOS"')

			client_id = token.get("x_zp_client_id")
			if client_id:
				headers["x-zp-client-id"] = client_id

			self._httpx_client = httpx.Client(
				cookies=token.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		return self._httpx_client

	def close(self) -> None:
		"""释放底层资源。Week 2 真实现后会关闭 httpx client 和 browser session。"""
		if self._httpx_client is not None:
			try:
				self._httpx_client.close()
			except Exception:
				pass
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
