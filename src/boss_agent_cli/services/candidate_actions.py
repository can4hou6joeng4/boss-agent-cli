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
#: ``NETWORK_ERROR`` 包含 HTTP 5xx 与超时 / 连接中断文案：写请求可能已在平台
#: 落库，不能当成明确拒绝释放预约（Issue #2316）。
_UNKNOWN_CODES = frozenset({"ACTION_UNCONFIRMED", "UNKNOWN", "NETWORK_ERROR"}) | frozenset(RISK_ERROR_BY_CODE)


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


class CandidateActionReleaseFailed(Exception):
	"""请求可证明未发出或平台明确拒绝，但释放本地预约失败。

	请求本身可以重试（限流 / 参数 / 登录态 / 预算停止），但 pending 行仍在：
	直接重试会撞上 ``ACTION_UNCONFIRMED``。命令层必须输出不可重试的本地状态
	错误，说明「本次请求未产生平台记录，但本地预约未释放」，而不是结果未知
	（Issues #2315 #2944 #2971）。
	"""

	def __init__(self, action: CandidateAction, cause: BaseException, claim_reference: str | None = None) -> None:
		super().__init__(str(cause) or type(cause).__name__)
		self.action = action
		self.cause = cause
		self.claim_reference = claim_reference


def candidate_release_reference(
	cache: CacheStore, job_id: str, action: CandidateAction, *, account: str = ""
) -> str | None:
	"""只为已证明未发送且释放失败的预约获取恢复标识；读取失败时禁止猜测。"""
	try:
		reference = cache.candidate_action_release_reference(job_id, action, account=account)
	except Exception:
		return None
	if isinstance(reference, str) and len(reference) == 32 and all(c in "0123456789abcdef" for c in reference):
		return reference
	return None


def candidate_release_recovery_action(reference: str | None, hint_command: Callable[[str], str]) -> str:
	"""恢复只针对当前预约；标识拿不到时保留 pending，交给人工维护。"""
	if reference is None:
		return (
			"先确认数据目录可写（磁盘空间、缓存目录权限）；本地预约标识无法读取，"
			"请备份缓存后联系维护者处理该预约，勿删除整库或直接重发"
		)
	clean_cmd = hint_command(f"clean --pending-claims {reference}")
	return (
		"先确认数据目录可写（磁盘空间、缓存目录权限），"
		f"再用 {clean_cmd} 清除本次残留预约，然后显式重新执行未完成的动作"
	)


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
	# 浏览器通道在写出前中断时，平台层会把严格的 request_sent=False 证据挂在
	# 响应上（Issue #3105）。这类失败没有产生任何平台写入，必须按可重试释放
	# 预约；只按错误码把 NETWORK_ERROR 归 unknown 会让职位被 pending 永久挡住。
	# 与异常侧 was_provably_unsent 同口径，不按 outcome 文本或 truthiness 推断。
	if response_provably_unsent(response):
		return "retryable"
	code, _ = platform.parse_error(response)
	if str(code) in _UNKNOWN_CODES:
		return "unknown"
	return "retryable"


def exception_reports_request_unsent(exc: BaseException) -> bool:
	"""异常本身是否带着「写请求确定未发出」的显式证据（``request_sent=False``）。

	只认底层客户端在**写入边界**标注的属性，不按异常类型推断。复合动作（写前
	读取 → 写 → 写后回读）里同一个异常类型可能发生在写之后，类型判定会把
	「结果未知」错放成可重试；反过来，写前读失败又会被一律当成结果未知
	（``hr request-resume``，Issue #2898）。因此 ``exchange_request_by_friend``
	这类复合调用在写前阶段自行标注本属性，调用方只消费证据。

	与 ``details.request_sent`` 是同一语义的两种载体：前者用于 ``WorkflowActionError``
	等带 details 的类型，本属性用于跨层传递时不便改 details 的异常实例。
	"""
	try:
		return getattr(exc, "request_sent", None) is False
	except Exception:
		# 异常对象自身的属性访问失败不能反转判定：保留 pending 是安全方向。
		return False


def was_provably_unsent(exc: BaseException) -> bool:
	"""异常是否证明请求没有发出（可以安全释放预约）。

	四类证据：
	- ``details.request_sent is False``：预算 / 时限在调度前抛出的停止；
	- 异常实例上的 ``request_sent is False``：底层客户端在写入边界标注的显式
	  证据（见 :func:`exception_reports_request_unsent`）；
	- ``BrowserSourceUnavailable``：浏览器通道全部不可用，请求从未发出；
	- 带 ``replay_safe=True`` 的 typed 写错误（wizard 的 ``WorkflowActionError``）
	  且不是风控 / ``UNKNOWN`` / ``ACTION_UNCONFIRMED``：平台已返回明确失败。

	其余异常（超时、连接中断、风控）都可能已经产生平台写入，必须保留 pending。
	"""
	if exception_reports_request_unsent(exc):
		return True
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
	"""单请求写操作（greet / apply / exchange / mark / 职位上下线）的异常是否证明请求尚未派发。

	这些动作每次只发一个浏览器 fetch 或一个表单请求。下面这些异常都发生在
	``BrowserSession._send_fetch`` 之前，异常本身却不带 ``request_sent`` 证据：

	- ``AuthRequired`` / ``TokenRefreshFailed``：建立浏览器会话时取登录态失败；
	- ``ProxyConfigurationError``：通道启动前解析代理环境变量失败；
	- ``BrowserNavigationError``：首页导航在本次 fetch 之前失败；
	- ``RequestTextEncodingError``：请求参数在编码阶段就失败（孤立代理项等），
	  一个字节都没有发出（Issue #2533）；
	- ``httpx`` 的连接 / 代理 / 协议选择异常（``was_request_provably_unsent``）：
	  请求在发出前就终结，招聘者写预约同样必须释放（Issue #2596）；
	- Playwright 内核缺失（普通 ``OSError`` / ``RuntimeError``，按文本识别）。

	不释放预约会让该职位被 pending 永久挡住：没有 TTL，也没有清理命令
	（Issue #2296）；wizard 侧则以同一判定撤销预落的写标记（Issue #2441）。
	这些类型**不能**并入共享的 ``was_provably_unsent``，也**不能**用于复合
	动作：招聘者侧在写之后还有读取，同一个类型（例如附件下载在传输之后才报
	``AuthRequired``）在那些路径上不证明写请求未发出。
	"""
	if was_provably_unsent(exc):
		return True

	from boss_agent_cli.api._base_client import RequestTextEncodingError, was_request_provably_unsent
	from boss_agent_cli.api.browser_client import BrowserNavigationError
	from boss_agent_cli.api.proxy import ProxyConfigurationError
	from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
	from boss_agent_cli.services.browser_runtime import is_browser_kernel_missing

	if was_request_provably_unsent(exc):
		return True
	if isinstance(
		exc,
		(AuthRequired, TokenRefreshFailed, ProxyConfigurationError, BrowserNavigationError, RequestTextEncodingError),
	):
		return True
	try:
		text = str(exc)
	except Exception:
		# 异常对象自身无法转成文本（如携带超长整数的 ValueError）时不能证明
		# 写请求未发出：fail-open 保留原始异常继续传播，不放宽预约释放判定
		# （Issue #2376）。
		return False
	return is_browser_kernel_missing(text)


def outcome_unknown_cause(exc: BaseException | None) -> dict[str, Any]:
	"""结果未知信封的 ``cause`` 字段：只放错误码 / 类型名，不复制原始消息。

	平台异常文本可能夹带临时凭据（Issue #2157），而 ``output.py`` 只按键名脱敏。
	"""
	if exc is None:
		return {}
	code = getattr(exc, "code", None)
	return {"cause": code if isinstance(code, str) and code else type(exc).__name__}


def cli_error_details(response: Any) -> dict[str, Any]:
	"""提取平台层挂在响应上的 ``__cli_error_details__``（如 ``replay_safe=false``）。

	内部命名空间的键（``__`` 前缀）不进入信封：调用方拿到的只是可对外透传的
	判定字段。四个求职者侧写命令（greet / apply / exchange / mark）共用同一份
	提取口径，避免各入口的 ``outcome_unknown`` / ``replay_safe`` 字段各自漂移。
	"""
	details = response.get("__cli_error_details__") if isinstance(response, dict) else None
	if not isinstance(details, dict):
		return {}
	return {str(key): value for key, value in details.items() if not str(key).startswith("__")}


def response_provably_unsent(response: Any) -> bool:
	"""响应级证据：平台客户端明确标注本次写请求没有发出。

	只认严格的 ``__cli_error_details__.request_sent is False``；``0`` / ``"false"``
	等类型漂移与缺失证据都不能把结果未知的请求降级成可重试（Issue #3105）。
	"""
	return cli_error_details(response).get("request_sent") is False


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
			# 释放本身也可能写失败（database is locked / 磁盘满）。无论原始异常
			# 是平台明确拒绝（Issue #2944）还是派发前的认证 / 预算 / 代理失败
			# （Issue #2315），只要预约没删掉，照原错误的恢复动作重试都会撞上
			# 残留的 pending 并落成不可恢复的 ACTION_UNCONFIRMED。统一转成不可
			# 重试的本地状态错误，让恢复指引先清理本地预约（Issue #2971）。
			try:
				release_candidate_action(cache, job_id, action, account=account)
			except Exception as release_exc:
				raise CandidateActionReleaseFailed(
					action, release_exc, candidate_release_reference(cache, job_id, action, account=account)
				) from release_exc
		raise
	outcome = classify(result) if classify is not None else "success"
	if outcome == "success":
		try:
			settle_candidate_action(cache, security_id, job_id, action, account=account)
		except Exception as exc:
			raise CandidateActionNotRecorded(action, exc) from exc
	elif outcome == "retryable":
		# 平台明确拒绝且没有产生记录：释放预约，修正参数 / 等待限流后可重试。
		try:
			release_candidate_action(cache, job_id, action, account=account)
		except Exception as exc:
			# 平台没接受请求，但 pending 行还在：直接重试会被预约拦住。报明确的
			# 本地状态错误，而不是让下一次调用落成 ACTION_UNCONFIRMED。
			raise CandidateActionReleaseFailed(
				action, exc, candidate_release_reference(cache, job_id, action, account=account)
			) from exc
	# outcome == "unknown"：保留 pending，后续调用按结果未知处理。
	return result
