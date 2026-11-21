"""求职者侧写动作（greet / apply）的跨进程预约与统一收口。

命令层原来的流程是 ``is_greeted`` / ``is_applied`` 检查 → 发送 → ``record_*``
记账，三步跨进程不原子：两个进程可以同时通过检查、各发一次真实请求
（Issue #1508）。这里按 ``(account, job_id, action)`` 在 SQLite 里用
``INSERT OR IGNORE`` 预约一个 pending 行，只有一个进程能抢到：

- 抢不到且已有 ``sent`` → ``ALREADY_GREETED`` / ``ALREADY_APPLIED``；
- 抢不到且仍是 ``pending``（上一次进程在发送窗口内退出）→
  ``ACTION_UNCONFIRMED``：结果未知、禁止重试；
- 抢到后平台确认成功 → settle 成成功记录（旧表 + 预约 sent）；
- 平台明确失败、或抛出可证明「请求未发出」的异常（``request_sent=false``，
  以及候选人单次请求特有的派发前失败，见 ``candidate_action_provably_unsent``）
  → 释放 pending，允许后续重试；
- 风控 / 超时 / ``UNKNOWN`` / 进程中断 → 保留 pending，后续按结果未知处理。

旧 ``greet_records`` / ``apply_records`` 仍按既有 ``is_greeted`` /
``is_applied`` 口径参与判断，历史去重不会因为新表出现而失效。
"""

from collections.abc import Callable
from typing import Any, Literal, TypeVar

from boss_agent_cli.api.browser_source import BrowserSourceUnavailable
from boss_agent_cli.api.client import RISK_ERROR_BY_CODE
from boss_agent_cli.cache.store import CacheStore

CandidateAction = Literal["greet", "apply"]
Outcome = Literal["success", "retryable", "unknown"]
T = TypeVar("T")

_ALREADY_CODE: dict[str, str] = {"greet": "ALREADY_GREETED", "apply": "ALREADY_APPLIED"}
_ALREADY_MESSAGE: dict[str, str] = {
	"greet": "已向该招聘者打过招呼",
	"apply": "已对该职位发起过投递/立即沟通",
}
_ACTION_LABEL: dict[str, str] = {"greet": "打招呼", "apply": "投递"}
#: 无法证明请求没有生效的错误码：保留 pending，禁止自动重放。
_UNKNOWN_CODES = frozenset({"ACTION_UNCONFIRMED", "UNKNOWN"}) | frozenset(RISK_ERROR_BY_CODE)


class CandidateActionBlocked(Exception):
	"""本地已有预约：本次不发送。

	``code`` 只会是 ``ALREADY_GREETED`` / ``ALREADY_APPLIED``（已成功）或
	``ACTION_UNCONFIRMED``（pending，结果未知、禁止重试）。
	"""

	def __init__(self, code: str, message: str) -> None:
		super().__init__(message)
		self.code = code
		self.message = message


class CandidateActionNotRecorded(Exception):
	"""平台已确认成功、本地 settle 失败；调用方必须输出不可重试的本地状态错误。

	保留原始异常（``__cause__``）供日志诊断，但命令层不能把它当成可重试的
	发送失败：重跑会绕过位于 pending/sent 的预约再发一次。
	"""

	def __init__(self, action: CandidateAction, cause: BaseException) -> None:
		super().__init__(str(cause) or type(cause).__name__)
		self.action = action
		self.cause = cause


def reserve_candidate_action(
	cache: CacheStore,
	security_id: str,
	job_id: str,
	action: CandidateAction,
	*,
	account: str = "",
) -> None:
	"""预约发送；已有成功记录或未决预约时抛 ``CandidateActionBlocked``。"""
	status = cache.claim_candidate_action(security_id, job_id, action, account=account)
	if status is None:
		return
	if status == "sent":
		raise CandidateActionBlocked(_ALREADY_CODE[action], _ALREADY_MESSAGE[action])
	raise CandidateActionBlocked(
		"ACTION_UNCONFIRMED",
		f"上一次{_ACTION_LABEL[action]}的预约仍在，结果未确认；禁止重试，请先到平台核对",
	)


def release_candidate_action(
	cache: CacheStore,
	job_id: str,
	action: CandidateAction,
	*,
	account: str = "",
) -> None:
	"""撤销确定未发出的预约（只删 pending）。"""
	cache.release_candidate_action(job_id, action, account=account)


def settle_candidate_action(
	cache: CacheStore,
	security_id: str,
	job_id: str,
	action: CandidateAction,
	*,
	account: str = "",
) -> None:
	"""平台确认成功：写历史去重表，再把预约标记为 sent。

	先写旧表再改预约状态：旧表写入失败时预约仍是 pending，后续重跑会按
	结果未知拦下；预约更新失败时旧表已能阻止重复发送。
	"""
	if action == "greet":
		cache.record_greet(security_id, job_id, account=account)
	else:
		cache.record_apply(security_id, job_id, account=account)
	cache.mark_candidate_action_sent(security_id, job_id, action, account=account)


def classify_platform_response(platform: Any, response: Any) -> Outcome:
	"""按平台响应判断动作结局：成功 / 明确失败（可重试）/ 结果未知。"""
	if platform.is_success(response):
		return "success"
	code, _ = platform.parse_error(response)
	if str(code) in _UNKNOWN_CODES:
		return "unknown"
	return "retryable"


def was_provably_unsent(exc: BaseException) -> bool:
	"""异常是否证明请求没有发出（可以安全释放预约）。

	三类证据：
	- ``details.request_sent is False``：预算 / 时限在调度前抛出的停止；
	- ``BrowserSourceUnavailable``：浏览器通道全部不可用，请求从未发出；
	- 带 ``replay_safe=True`` 的 typed 写错误（wizard 的 ``WorkflowActionError``）
	  且不是风控 / ``UNKNOWN`` / ``ACTION_UNCONFIRMED``：平台已返回明确失败。

	其余异常（超时、连接中断、风控）都可能已经产生平台写入，必须保留 pending。
	"""
	details = getattr(exc, "details", None)
	if isinstance(details, dict) and details.get("request_sent") is False:
		return True
	if isinstance(exc, BrowserSourceUnavailable):
		return True
	code = getattr(exc, "code", None)
	if not isinstance(code, str) or code in _UNKNOWN_CODES:
		return False
	return getattr(exc, "replay_safe", None) is True


def candidate_action_provably_unsent(exc: BaseException) -> bool:
	"""候选人单次写请求（greet / apply）的异常是否证明请求尚未派发。

	``platform.greet/apply`` 每次只发一个浏览器 fetch。下面这些异常都发生在
	``BrowserSession._send_fetch`` 之前，异常本身却不带 ``request_sent`` 证据：

	- ``AuthRequired`` / ``TokenRefreshFailed``：建立浏览器会话时取登录态失败；
	- ``ProxyConfigurationError``：通道启动前解析代理环境变量失败；
	- ``BrowserNavigationError``：首页导航在本次 fetch 之前失败；
	- Playwright 内核缺失（普通 ``OSError`` / ``RuntimeError``，按文本识别）。

	不释放预约会让该职位被 pending 永久挡住：没有 TTL，也没有清理命令
	（Issue #2296）。这些类型**不能**并入共享的 ``was_provably_unsent``：
	招聘者侧的复合动作在写之后还有读取，同一个类型（例如附件下载在传输之后
	才报 ``AuthRequired``）在那些路径上不证明写请求未发出。
	"""
	if was_provably_unsent(exc):
		return True

	from boss_agent_cli.api.browser_client import BrowserNavigationError
	from boss_agent_cli.api.proxy import ProxyConfigurationError
	from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
	from boss_agent_cli.services.browser_runtime import is_browser_kernel_missing

	if isinstance(exc, (AuthRequired, TokenRefreshFailed, ProxyConfigurationError, BrowserNavigationError)):
		return True
	return is_browser_kernel_missing(str(exc))


def outcome_unknown_cause(exc: BaseException | None) -> dict[str, Any]:
	"""结果未知信封的 ``cause`` 字段：只放错误码 / 类型名，不复制原始消息。

	平台异常文本可能夹带临时凭据（Issue #2157），而 ``output.py`` 只按键名脱敏。
	"""
	if exc is None:
		return {}
	code = getattr(exc, "code", None)
	return {"cause": code if isinstance(code, str) and code else type(exc).__name__}


def run_candidate_action(
	cache: CacheStore,
	*,
	security_id: str,
	job_id: str,
	action: CandidateAction,
	account: str = "",
	send: Callable[[], T],
	classify: Callable[[T], Outcome] | None = None,
) -> T:
	"""预约 → 发送 → settle / release 的唯一收口。

	``send`` 只负责发请求，可能抛出预算 / 风控 / 平台异常；``classify`` 用于
	把「返回失败响应」的平台调用映射成结局（wizard 的 ``_write_call`` 失败时
	直接抛异常，不需要 classify，默认成功）。

	发送成功后 settle 失败会包装成 ``CandidateActionNotRecorded``，让四个入口
	统一输出不可重试的本地状态错误。
	"""
	reserve_candidate_action(cache, security_id, job_id, action, account=account)
	try:
		result = send()
	except BaseException as exc:
		if candidate_action_provably_unsent(exc):
			release_candidate_action(cache, job_id, action, account=account)
		raise
	outcome = classify(result) if classify is not None else "success"
	if outcome == "success":
		try:
			settle_candidate_action(cache, security_id, job_id, action, account=account)
		except Exception as exc:
			raise CandidateActionNotRecorded(action, exc) from exc
	elif outcome == "retryable":
		# 平台明确拒绝且没有产生记录：释放预约，修正参数 / 等待限流后可重试。
		release_candidate_action(cache, job_id, action, account=account)
	# outcome == "unknown"：保留 pending，后续调用按结果未知处理。
	return result
