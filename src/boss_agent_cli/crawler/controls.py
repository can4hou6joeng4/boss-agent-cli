"""Transport-independent crawl stop signals; they must never trigger fallback."""


class CrawlLimitExceeded(RuntimeError):
	"""A user-configured crawl budget has reached its hard limit."""

	request_control_stop = True


class CrawlStopRequested(RuntimeError):
	"""The user requested a checkpoint stop at the next safe point."""

	request_control_stop = True


class CrawlWorkflowStopRequested(CrawlStopRequested):
	"""The owning workflow requested cooperative stop."""


class CrawlTimeoutRequested(CrawlStopRequested):
	"""The owning workflow deadline expired."""


class CrawlRuntimeUnavailable(RuntimeError):
	"""The optional crawl dependencies cannot be imported; retrying or resuming cannot change that."""


class CrawlExportError(RuntimeError):
	"""采集结果无法写出到本地：确定性失败，重试或恢复同一断点无法改变结果。"""


class CrawlProfileBusy(RuntimeError):
	"""采集配置目录正被另一个运行中的采集 Chrome 使用；本次调用没有发出任何请求。"""


class CrawlProfilePathInvalid(RuntimeError):
	"""采集配置目录无法创建或不是目录：确定性本地路径失败，重试或恢复只会复现（Issue #1290）。"""


class CrawlSessionAuthError(RuntimeError):
	"""采集浏览器无法注入本地登录态：这是本地 Cookie / CDP 准备失败，不是平台风控。

	调用方必须按认证前置条件回报；``crawler.service._run`` 不得为它写
	``cdp_risk_lock``，否则用户会被错误地要求执行 ``boss clean --risk-lock``
	（Issue #1405）。真正的安全页仍然抛 ``CrawlRiskError``，风控硬边界不变。
	"""
