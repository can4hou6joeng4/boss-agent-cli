"""Sequential crawl orchestration, checkpointing and incremental export."""

from __future__ import annotations

import math
import random
import socket
import stat
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, replace
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable

from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.models import field_text, safe_text
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import (
	RequestBudget,
	RequestBudgetExceeded,
	cancellable_sleep,
	check_request_control,
	is_request_stop,
	request_control,
)
from boss_agent_cli.api.zhipin_errors import is_platform_success_code, normalize_risk_code
from boss_agent_cli.cache.store import (
	CacheBusyError,
	CacheDatabaseInvalid,
	CacheRecordInvalid,
	CacheStore,
)
from boss_agent_cli.compliance import require_capability_mode
from boss_agent_cli.crawler.controls import (
	CrawlExportError as CrawlExportError,
	CrawlHookDirInvalid as CrawlHookDirInvalid,
	CrawlLimitExceeded as CrawlLimitExceeded,
	CrawlProfileBusy as CrawlProfileBusy,
	CrawlProfilePathInvalid as CrawlProfilePathInvalid,
	CrawlRuntimeUnavailable as CrawlRuntimeUnavailable,
	CrawlSessionAuthError as CrawlSessionAuthError,
	CrawlStopRequested as CrawlStopRequested,
	CrawlWorkflowStopRequested as CrawlWorkflowStopRequested,
	CrawlTimeoutRequested as CrawlTimeoutRequested,
)
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.crawler.hooks import (
	HookInjection,
	HookRegistrationError,
	verify_hook_profile,
)
from boss_agent_cli.crawler.transport import CrawlRiskError, CrawlTransport
from boss_agent_cli.display import boss_command, risk_error_message, shell_argument
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path
from boss_agent_cli.services.text_files import sanitize_surrogates


class CrawlBusyError(RuntimeError):
	"""同一个 crawl run 正在由另一次调用执行：本次调用没有发出任何请求。"""

	def __init__(self, run_id: str) -> None:
		super().__init__(f"crawl run {run_id} 正在由另一次调用执行")
		self.run_id = run_id


class PagesOverrideError(ValueError):
	"""resume 的 ``--pages`` 与断点进度矛盾：要调整的是本次参数，不是断点设置。"""


def validate_pages_override(next_page: int, pages: int) -> None:
	"""resume 的 ``--pages`` 不能缩到断点进度之前（Issue #2061）。

	``pages`` 是采集范围的严格上限，断点的 ``next_page`` 最多是 ``pages + 1``
	（与 cache 记录校验同一口径，Issue #2030）。把上限缩到断点进度之前会让
	``params.pages`` 与 ``next_page`` 自相矛盾：先落盘、再被 ``_run`` 重读断点
	时判成记录损坏，之后 ``status`` / ``results`` / ``resume`` 全部拒绝访问，
	已采数据只能用 SQLite 手工取回。写盘之前按参数问题拒绝；不能静默 clamp
	——缩小采集范围是调用方的显式意图，改写语义会误导。
	"""
	if next_page <= pages + 1:
		return
	raise PagesOverrideError(
		f"--pages {pages} 小于断点进度：任务已推进到第 {next_page} 页，"
		f"页数上限不能小于 {next_page - 1}；要按更小页数采集请用 crawl start 新建任务"
	)


@dataclass(frozen=True)
class CrawlSettings:
	query: str
	city_code: str
	pages: int
	with_detail: bool
	profile_path: Path
	chrome_path: str | None
	cdp_port: int
	hook_profile: str
	hook_dir: Path | None = None
	hook_source: str = "user-supplied"
	max_requests: int = 20
	max_details: int = 50
	max_seconds: int = 600
	max_retries: int = 1
	operating_mode: str = "assisted"

	def as_dict(self) -> dict[str, Any]:
		return {
			"query": self.query,
			"city_code": self.city_code,
			"pages": self.pages,
			"with_detail": self.with_detail,
			"browser": {
				"profile_path": str(self.profile_path),
				"cdp_port": self.cdp_port,
				"process_owner": "crawler",
				"cleanup": "quit",
				"port_policy": "must_be_unused",
			},
			"chrome_path": self.chrome_path,
			"hook": {
				"profile": self.hook_profile,
				"source": self.hook_source,
				"directory": str(self.hook_dir) if self.hook_dir else None,
			},
			"limits": {
				"max_requests": self.max_requests,
				"max_details": self.max_details,
				"max_seconds": self.max_seconds,
				"max_retries": self.max_retries,
			},
			"operating_mode": self.operating_mode,
		}

	@classmethod
	def from_dict(cls, raw: dict[str, Any], *, run_id: str = "") -> "CrawlSettings":
		"""从断点记录还原设置；缺键与类型漂移按记录损坏处理（Issue #1117）。

		`as_dict()` 总是写全顶层字段，缺键只可能来自记录损坏或手工篡改。原来
		直接下标访问，`KeyError` 被命令层当成「未找到 crawl run: <字段名>」，
		把「本地记录写坏」误导成「任务不存在」，恢复指引指向新建任务；类型
		漂移（如 `pages="abc"`）则落成 INVALID_PARAM 或 INTERNAL_ERROR。
		"""
		browser = raw.get("browser", {})
		hook = raw.get("hook", {})
		limits = raw.get("limits", {})
		if not isinstance(browser, dict):
			browser = {}
		if not isinstance(hook, dict):
			hook = {}
		if not isinstance(limits, dict):
			limits = {}
		missing = [key for key in ("query", "city_code", "pages", "with_detail") if key not in raw]
		if missing:
			raise CacheRecordInvalid("crawl_run", run_id, "params", f"缺少字段 {', '.join(missing)}")
		query = raw["query"]
		city_code = raw["city_code"]
		pages = raw["pages"]
		with_detail = raw["with_detail"]
		if not isinstance(query, str):
			raise CacheRecordInvalid("crawl_run", run_id, "params.query", f"期望字符串，实际是 {type(query).__name__}")
		if not isinstance(city_code, str):
			raise CacheRecordInvalid(
				"crawl_run", run_id, "params.city_code", f"期望字符串，实际是 {type(city_code).__name__}"
			)
		# bool 是 int 的子类：`pages=true` 是类型漂移，不是第 1 页。
		if not isinstance(pages, int) or isinstance(pages, bool):
			raise CacheRecordInvalid("crawl_run", run_id, "params.pages", f"期望整数，实际是 {type(pages).__name__}")
		# 页数必须至少为 1：0 / 负数会通过读取，到 resume 才落成误导性的
		# INVALID_PARAM（configure 也没有 pages 选项）（Issue #1325）。
		if pages < 1:
			raise CacheRecordInvalid("crawl_run", run_id, "params.pages", f"必须是正整数，实际是 {pages}")
		# chrome_path 原样传给 transport，非字符串会在 set_browser_path 抛 TypeError，
		# 被 `_run` 记成 stopped 并给出「恢复」指引，重跑永远复现（Issue #1278）。
		chrome_path = raw.get("chrome_path")
		if chrome_path is not None and not isinstance(chrome_path, str):
			raise CacheRecordInvalid(
				"crawl_run", run_id, "params.chrome_path", f"期望字符串或 null，实际是 {type(chrome_path).__name__}"
			)
		if not isinstance(with_detail, bool):
			raise CacheRecordInvalid(
				"crawl_run", run_id, "params.with_detail", f"期望布尔值，实际是 {type(with_detail).__name__}"
			)
		profile_path = raw.get("profile_path") or browser.get("profile_path")
		cdp_port = raw.get("cdp_port") or browser.get("cdp_port")
		hook_profile = raw.get("hook_profile") or hook.get("profile")
		if profile_path is None or cdp_port is None or hook_profile is None:
			missing_browser = [
				name
				for name, value in (
					("browser.profile_path", profile_path),
					("browser.cdp_port", cdp_port),
					("hook.profile", hook_profile),
				)
				if value is None
			]
			raise CacheRecordInvalid("crawl_run", run_id, "params", f"缺少字段 {', '.join(missing_browser)}")
		# profile_path 为空串时 `Path("")` 就是 `Path(".")`：resume 会删除 cwd 下
		# 采集 Chrome 的 Singleton 锁并把浏览器配置写进当前目录；数字 / 对象漂移
		# 同样会落成相对 cwd 的目录名。断点里的配置目录必须是绝对路径
		# （Issue #1347）。
		if not isinstance(profile_path, str):
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"params.browser.profile_path",
				f"期望绝对路径字符串，实际是 {type(profile_path).__name__}",
			)
		if not profile_path.strip():
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"params.browser.profile_path",
				f"期望非空绝对路径字符串，实际是空字符串（{type(profile_path).__name__}）",
			)
		# NUL 等控制字符能通过「非空绝对路径」检查，但会在 transport 的
		# resolve() / mkdir() 抛 ValueError，被 `_run` 兜成 ok:true 的 stopped
		# （Issue #1482）。合法路径不可能包含这些字符，断点里出现只能是记录
		# 损坏或手工篡改。
		if any(ord(char) < 0x20 for char in profile_path):
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"params.browser.profile_path",
				f"期望绝对路径字符串，实际包含控制字符 {profile_path!r}",
			)
		if not Path(profile_path).is_absolute():
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"params.browser.profile_path",
				f"期望绝对路径，实际是相对路径 {profile_path!r}（{type(profile_path).__name__}）",
			)
		# 断点里的端口必须落在 1–65535：越界值会在 transport 的
		# socket.connect_ex 抛 OverflowError，被吞成 ok:true 的 stopped 并给出
		# 可复现的 resume 指引（Issue #1325）。
		try:
			parsed_cdp_port = crawl_cdp_port(cdp_port)
		except ValueError as exc:
			raise CacheRecordInvalid("crawl_run", run_id, "params.browser.cdp_port", str(exc)) from exc
		hook_profile = str(hook_profile)
		if hook_profile not in ("none", "screenshot-full"):
			raise CacheRecordInvalid(
				"crawl_run",
				run_id,
				"params.hook.profile",
				f"期望 none 或 screenshot-full，实际是 {hook_profile!r}",
			)
		hook_dir_raw = raw.get("hook_dir") or hook.get("directory")
		hook_dir = Path(str(hook_dir_raw)) if hook_dir_raw else None
		if hook_profile == "screenshot-full" and hook_dir is None:
			raise CacheRecordInvalid("crawl_run", run_id, "params.hook.directory", "screenshot-full 需要 hook 目录")

		def _limit(key: str, default: int, *, minimum: int) -> int:
			# 不能用 `value or default`：0 是损坏值，不能被静默替换成默认预算，
			# 否则 status 与 resume 对同一行口径不一致（Issue #1325）。
			value = raw.get(key)
			if value is None:
				value = limits.get(key, default)
			# bool 是 int 子类；float 会静默截断（2.9 → 2）。两者都按记录损坏
			# 处理，与 crawl_cdp_port / crawl_int_setting 同口径（Issue #1938）。
			if isinstance(value, bool) or not isinstance(value, (int, str)):
				raise CacheRecordInvalid(
					"crawl_run", run_id, f"params.limits.{key}", f"期望整数，实际是 {type(value).__name__}"
				)
			try:
				parsed = int(value)
			except (TypeError, ValueError, OverflowError) as exc:
				raise CacheRecordInvalid(
					"crawl_run", run_id, f"params.limits.{key}", f"字段类型无法解析（{exc}）"
				) from exc
			if parsed < minimum:
				raise CacheRecordInvalid(
					"crawl_run", run_id, f"params.limits.{key}", f"不能小于 {minimum}，实际是 {parsed}"
				)
			return parsed

		try:
			return cls(
				query=query,
				city_code=city_code,
				pages=pages,
				with_detail=with_detail,
				profile_path=Path(str(profile_path)),
				chrome_path=chrome_path,
				cdp_port=parsed_cdp_port,
				hook_profile=hook_profile,
				hook_dir=hook_dir,
				hook_source=str(raw.get("hook_source") or hook.get("source", "user-supplied")),
				max_requests=_limit("max_requests", 20, minimum=1),
				max_details=_limit("max_details", 50, minimum=1),
				max_seconds=_limit("max_seconds", 600, minimum=1),
				max_retries=_limit("max_retries", 1, minimum=0),
				operating_mode=str(raw.get("operating_mode", "assisted")),
			)
		except (TypeError, ValueError, OverflowError) as exc:
			# OverflowError：`1e400` 解析成 inf，`int(inf)` 抛错；与
			# crawl_int_setting / crawl_cdp_port 同口径按记录损坏处理（Issue #1288）。
			raise CacheRecordInvalid("crawl_run", run_id, "params", f"字段类型无法解析（{exc}）") from exc


def crawl_int_setting(cfg: Mapping[str, Any], key: str, default: int, *, minimum: int = 1) -> int:
	"""读取整数型 crawl 配置；显式值优先于默认值。

	不能用 `value or default`：`max_retries=0` 是 `crawl configure` 明确接受的值，
	用 or 会把 0 当成缺省并悄悄改回 1。
	"""
	value = cfg.get(key)
	if value is None:
		return default
	# bool 是 int 子类（true 会静默变成 1 秒预算）；float 会被 int() 静默截断。
	# 只接受整数与数字字符串，与 crawl_cdp_port 同口径（Issue #1938）。
	if isinstance(value, bool) or not isinstance(value, (int, str)):
		raise ValueError(f"crawl 配置项 {key} 必须是整数")
	try:
		parsed = int(value)
	# OverflowError：``1e400`` 解析成 inf，用户可修复，不能落成 INTERNAL_ERROR。
	except (TypeError, ValueError, OverflowError) as exc:
		raise ValueError(f"crawl 配置项 {key} 必须是整数") from exc
	if parsed < minimum:
		raise ValueError(f"crawl 配置项 {key} 不能小于 {minimum}")
	return parsed


def crawl_chrome_path(value: Any) -> str | None:
	"""校验 crawl 配置中的 chrome_path：字符串路径或 null。"""
	if value is None:
		return None
	if not isinstance(value, str):
		raise ValueError("crawl 配置项 chrome_path 必须是字符串或 null")
	return value


def crawl_cdp_port(value: Any) -> int:
	"""校验 crawl 配置中的 cdp_port：1-65535 的整数。"""
	# bool 是 int 子类（true 不是端口）；float / 1e400 的 int() 会截断或溢出，
	# 只接受整数与数字字符串，与 CLI 的 IntRange 同口径（Issue #1325）。
	if isinstance(value, bool) or not isinstance(value, (int, str)):
		raise ValueError("crawl 配置项 cdp_port 必须是整数")
	try:
		port = int(value)
	except (TypeError, ValueError, OverflowError) as exc:
		raise ValueError("crawl 配置项 cdp_port 必须是整数") from exc
	if not 1 <= port <= 65535:
		raise ValueError("crawl 配置项 cdp_port 必须在 1-65535 之间")
	return port


def unused_local_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


@dataclass(frozen=True)
class CrawlOutcome:
	run_id: str
	status: str
	next_page: int
	pages_completed: int
	jobs_seen: int
	detail_checks: int
	output_paths: dict[str, str]
	hooks: tuple[HookInjection, ...]
	requests_attempted: int = 0
	detail_requests_attempted: int = 0
	elapsed_seconds: int = 0
	error: str = ""
	risk_lock: dict[str, Any] | None = None
	data_dir: Path | None = None

	def as_dict(self) -> dict[str, Any]:
		result = {
			"run_id": self.run_id,
			"status": self.status,
			"next_page": self.next_page,
			"pages_completed": self.pages_completed,
			"jobs_seen": self.jobs_seen,
			"detail_checks": self.detail_checks,
			"checkpoint": {
				"next_page": self.next_page,
				"resume_command": boss_command(f"crawl resume {shell_argument(self.run_id)}", data_dir=self.data_dir),
			},
			"output_paths": self.output_paths,
			"requests_attempted": self.requests_attempted,
			"detail_requests_attempted": self.detail_requests_attempted,
			"elapsed_seconds": self.elapsed_seconds,
			"hooks": [
				{"name": item.name, "success": item.success, "sha256": item.sha256, "reason": item.reason}
				for item in self.hooks
			],
			"error": self.error,
		}
		if self.risk_lock is not None:
			result["risk_lock"] = self.risk_lock
		return result


class CrawlBudget:
	"""Conservative crawl pacing on top of the common cross-transport budget."""

	def __init__(
		self,
		cache: CacheStore,
		*,
		sleeper: Callable[[float], None] | None = None,
		clock: Callable[[], float] = time.time,
		random_delay: Callable[[float, float], float] = random.uniform,
	) -> None:
		self._cache = cache
		self._sleeper = sleeper
		self._clock = clock
		self._random_delay = random_delay
		self._check: Callable[[], None] | None = None

	def bind_check(self, check: Callable[[], None]) -> None:
		self._check = check

	def wait(self, kind: str, on_wait: Callable[[float], None] | None = None) -> None:
		check_request_control(self._check)
		low, high = (5.0, 10.0) if kind == "list" else (3.0, 6.0)
		interval = self._random_delay(low, high)
		now = self._clock()
		remaining = self._cache.reserve_crawl_budget("zhipin:all", now=now, interval=interval)
		if remaining > 0:
			if on_wait is not None:
				on_wait(remaining)
			cancellable_sleep(remaining, check=self._check, sleeper=self._sleeper)
		check_request_control(self._check)


class CrawlRunBudget:
	"""Per-run hard caps for requests, detail requests, time, and retries."""

	def __init__(
		self,
		settings: CrawlSettings,
		*,
		requests_attempted: int = 0,
		detail_requests_attempted: int = 0,
		elapsed_seconds: int = 0,
		on_change: Callable[["CrawlRunBudget"], None] | None = None,
		clock: Callable[[], float] = time.monotonic,
	) -> None:
		self._settings = settings
		self._clock = clock
		self._started_at = clock()
		self._elapsed_before_start = elapsed_seconds
		self._on_change = on_change
		self.requests_attempted = requests_attempted
		self.detail_requests_attempted = detail_requests_attempted

	def check_list_request_capacity(self, transport: CrawlTransport, page_no: int, *, before_open: bool = False) -> None:
		"""在多次导航开始前检查请求下限，实际请求仍逐次认领预算。"""
		minimum_list_requests = getattr(transport, "minimum_list_requests", None)
		if not callable(minimum_list_requests):
			return
		required = minimum_list_requests(page_no, before_open=before_open)
		remaining = max(0, self._settings.max_requests - self.requests_attempted)
		if required > remaining:
			raise CrawlLimitExceeded(f"到达采集断点第 {page_no} 页至少需要 {required} 次请求，剩余预算为 {remaining} 次")

	def claim_request(self, kind: str) -> None:
		self.check_time()
		if self.requests_attempted >= self._settings.max_requests:
			raise CrawlLimitExceeded("request budget exhausted")
		if kind == "detail" and self.detail_requests_attempted >= self._settings.max_details:
			raise CrawlLimitExceeded("detail-request budget exhausted")
		self.requests_attempted += 1
		if kind == "detail":
			self.detail_requests_attempted += 1
		if self._on_change is not None:
			self._on_change(self)

	def check_time(self) -> None:
		if self.elapsed_seconds >= self._settings.max_seconds:
			raise CrawlLimitExceeded("wall-clock budget exhausted")

	@property
	def elapsed_seconds(self) -> int:
		return self._elapsed_before_start + max(0, int(self._clock() - self._started_at))


def crawl_execution_lock(data_dir: Path, run_id: str) -> AbstractContextManager[bool]:
	return exclusive_file_lock(run_lock_path(data_dir / "cache" / "crawl-locks", run_id))


#: worker 从被拉起到取得执行锁只需要解释器启动与一次本地读取；超过这段时间
#: 仍是 queued 且锁空闲，说明它没有活下来（Issue #1045）。
QUEUE_HANDOFF_GRACE_SECONDS = 30.0

#: 交接新鲜度允许的未来偏斜（与 ``cache/store.py`` 的未来偏斜同口径）。
#:
#: 写入方用 ``time.time()`` 落 ``updated_at``；轻微时钟偏斜仍应视为「刚排队」。
#: 更远的有限未来值只可能是漂移（时钟快进后校正、手工写坏的库），必须按
#: 排队已久处理，否则任务永远留在交接窗口里（Issue #2193）。
QUEUE_FUTURE_SKEW_SECONDS = 60.0


def tolerated_timestamp(value: Any) -> float:
	"""本地记录时间戳的容错读取：漂移值按 0 参与调度（与 Issue #1192 同口径）。

	``get_crawl_run`` 故意不校验 ``created_at`` / ``updated_at``，消费方必须自行
	降级；纯本地元数据问题不能拦停恢复，更不能被兜成可重试的 NETWORK_ERROR
	（Issue #1206）。``inf`` / ``nan`` 同样按 0：``time.time() - inf`` 是
	``-inf``，小于交接窗口，会让 queued 任务被永远当成「刚排队」，后台恢复返回
	成功却不启动 worker（Issue #1349）；超大整数在 ``float()`` 抛
	``OverflowError``，也不能逃成 INTERNAL_ERROR。
	"""
	try:
		parsed = float(value or 0.0)
	except (TypeError, ValueError, OverflowError):
		return 0.0
	return parsed if math.isfinite(parsed) else 0.0


def crawl_run_awaiting_worker(run: Mapping[str, Any]) -> bool:
	"""任务刚排队：原 worker 可能正要取锁，调用方不探测、不抢占。"""
	if run["status"] != "queued":
		return False
	# 时钟回拨得到负值时按仍在窗口内处理，宁可多等也不抢；时间戳类型漂移
	# 无法判断新鲜度，按「排队已久」去探测锁，不能让调用方 traceback（Issue #1192）。
	now = time.time()
	updated = tolerated_timestamp(run["updated_at"])
	# 有限但远在未来的值是漂移：减法得到巨大负数，任务会被永远当成刚排队，
	# 后台恢复不拉起 worker、status 也不报 queue_overdue（与 Issue #1349 的
	# inf/nan 同一失败模式）。超过允许偏斜的按排队已久去探测执行锁。
	if updated > now + QUEUE_FUTURE_SKEW_SECONDS:
		return False
	return now - updated < QUEUE_HANDOFF_GRACE_SECONDS


def crawl_executor_active(data_dir: Path, run_id: str) -> bool:
	"""探测 crawl run 是否仍有执行器持有执行锁。

	``crawl status`` / ``crawl stop`` 只读 DB 里的 ``running`` 无法区分「真的在
	跑」与「执行器被 SIGKILL、内核已释放锁」；不探测就会出现 stop 假成功与永远
	running 的状态页（Issue #2465）。探测会短暂取得再释放执行锁，语义与
	``_prepare_background_resume`` 的检查一致；只读命令不改任务状态。

	锁文件不存在时直接判否：执行器只在取锁之后才把状态写成 queued / running，
	路径为空等价于没有执行器，不必为一个只读查询创建 / 删除锁文件。
	"""
	path = run_lock_path(data_dir / "cache" / "crawl-locks", run_id)
	try:
		path.lstat()
	except FileNotFoundError:
		return False
	with exclusive_file_lock(path) as acquired:
		return not acquired


class CrawlService:
	"""Run an explicit browser crawl and retain every recovery-relevant state."""

	def __init__(
		self,
		cache: CacheStore,
		*,
		data_dir: Path,
		transport_factory: Callable[[CrawlSettings], CrawlTransport],
		budget_factory: Callable[[CacheStore], CrawlBudget] = CrawlBudget,
		control_probe: Callable[[], str | None] | None = None,
		on_run_created: Callable[[str], None] | None = None,
		preflight: Callable[[], None] | None = None,
	) -> None:
		self._cache = cache
		self._data_dir = data_dir
		self._transport_factory = transport_factory
		self._budget_factory = budget_factory
		self._control_probe = control_probe
		self._on_run_created = on_run_created
		# 登录态 / 浏览器运行时的本地预检。命令层只在确认还有恢复工作之后
		# 调用它：已完成的 run 直接返回既有结果（Issue #1412）。
		self._preflight = preflight
		self._unpersisted_risk_lock: cdp_risk_lock.RiskLock | None = None
		self._request_budget = RequestBudget.for_data_dir(data_dir)

	def execution_lock(self, run_id: str) -> AbstractContextManager[bool]:
		"""同一 crawl run 的执行权：跨进程独占、不等待，进程退出时由操作系统释放。

		每个 run 使用独立锁。执行和无执行器时的停止结算共用这把锁。
		"""
		return crawl_execution_lock(self._data_dir, run_id)

	@contextmanager
	def _execution_right(self, run_id: str) -> Iterator[None]:
		"""取得执行权，取不到就按「正在执行」拒绝，而不是排队等前一个执行器。

		必须在读取断点之前取得：两次调用都读到旧游标时，后到者会从同一个页
		重抓，把重复请求打给平台（Issue #1023）。
		"""
		with self.execution_lock(run_id) as acquired:
			if not acquired:
				raise CrawlBusyError(run_id)
			yield

	def create_and_run(self, settings: CrawlSettings) -> CrawlOutcome:
		run_id = self.create(settings)
		# on_run_created 发布 run_id 后，别的调用方可能已经推进了断点。新建与恢复
		# 共用持锁后的断点读取，不能再假定 next_page=1（Issue #1023）。
		return self.resume(run_id)

	def create(self, settings: CrawlSettings) -> str:
		"""Persist a new task so another process may run or resume it."""
		self._check_risk_lock()
		self._check_egress_consistency()
		self._validate_settings(settings)
		require_capability_mode(settings.operating_mode, "crawl")
		require_capability_mode(settings.operating_mode, "crawl-cdp")
		if settings.hook_profile != "none":
			require_capability_mode(settings.operating_mode, "crawl-hook")
		run_id = uuid.uuid4().hex[:12]
		# 输出目录必须按当前工作目录解析成绝对路径再落盘：`--data-dir` 允许
		# 相对路径，原样持久化会让换个 cwd 的 resume 把产物写到无关目录
		# （Issue #1481）。
		try:
			output_dir = (self._data_dir / "crawl" / "runs" / run_id).resolve()
		except RuntimeError as exc:
			# Path.resolve 在 3.11 / 3.12 遇到符号链接环抛 RuntimeError（3.13+ 不抛）：
			# 确定性本地路径失败，落到入口兜底会被报成可重试的 NETWORK_ERROR（Issue #2169）。
			raise CrawlProfilePathInvalid(
				f"采集输出目录 {str(self._data_dir / 'crawl' / 'runs' / run_id)!r} 无法解析（{exc}）；"
				"请检查数据目录下是否存在符号链接环，修正后重新创建任务"
			) from exc
		self._cache.create_crawl_run(run_id, settings.as_dict(), str(output_dir), status="queued")
		if self._on_run_created is not None:
			self._on_run_created(run_id)
		return run_id

	def _check_egress_consistency(self) -> None:
		"""Refuse to start a run whose exit contradicts the locale it declares.

		Inert unless ``BOSS_EGRESS_PROBE_URL`` is configured, so no probe traffic
		happens by default and an unknown exit never blocks work.
		"""
		from boss_agent_cli.api.egress import ensure_egress_consistent, probe_egress
		from boss_agent_cli.api.endpoints import DEFAULT_HEADERS

		verdict = probe_egress()
		if not verdict.exit_country:
			return
		ensure_egress_consistent(
			exit_country=verdict.exit_country,
			accept_language=str(DEFAULT_HEADERS.get("Accept-Language", "")),
			timezone="Asia/Shanghai",
		)

	def resume(
		self,
		run_id: str,
		*,
		pages: int | None = None,
		with_detail: bool = False,
		operating_mode: str | None = None,
		clear_stop: bool = False,
	) -> CrawlOutcome:
		# 执行权先于断点读取：重叠恢复会在两个执行器各读一份旧游标后重复抓页，
		# 再把同一页的请求打给平台（Issue #1023）。取不到就拒绝，不排队。
		with self._execution_right(run_id):
			return self._resume_locked(
				run_id,
				pages=pages,
				with_detail=with_detail,
				operating_mode=operating_mode,
				clear_stop=clear_stop,
			)

	def has_resume_work(self, run: dict[str, Any], *, pages: int | None = None, with_detail: bool = False) -> bool:
		"""只读判断快照是否还有恢复工作；真正执行前仍须取锁并重读断点。"""
		if run["status"] != "completed":
			return True
		needs_more_pages = not bool(run["list_finished"]) and pages is not None and pages >= int(run["next_page"])
		pending_details = with_detail and any(
			not item["detail_done"] for item in self._cache.list_crawl_jobs(str(run["run_id"]))
		)
		return needs_more_pages or pending_details

	def _resume_locked(
		self,
		run_id: str,
		*,
		pages: int | None,
		with_detail: bool,
		operating_mode: str | None,
		clear_stop: bool,
	) -> CrawlOutcome:
		run = self._cache.get_crawl_run(run_id)
		if run is None:
			raise KeyError(run_id)
		settings = CrawlSettings.from_dict(run["params"], run_id=run_id)
		active_mode = operating_mode or settings.operating_mode
		require_capability_mode(active_mode, "crawl")
		require_capability_mode(active_mode, "crawl-cdp")
		if settings.hook_profile != "none":
			require_capability_mode(active_mode, "crawl-hook")
		settings = replace(settings, operating_mode=active_mode)
		if pages is not None:
			settings = replace(settings, pages=pages)
		if with_detail:
			settings = replace(settings, with_detail=True)
		self._validate_settings(settings)
		# --pages 与断点进度的矛盾必须在 completed 快速路径之前判定：已完成任务
		# 若绕过校验，会把「缩小页数上限」静默接受成成功，与 stopped / queued
		# 任务的 INVALID_PARAM 口径不一致（Issue #2386，同 #2061 的不能静默
		# clamp 契约）。
		if pages is not None:
			validate_pages_override(int(run["next_page"]), settings.pages)
		if not self.has_resume_work(run, pages=pages, with_detail=with_detail):
			output_dir = Path(str(run["output_dir"]))
			if not _artifacts_present(output_dir):
				# 产物被删除 / 清理后 completed 快速路径仍会报成功并指向不存在的
				# 文件（Issue #1873）：用本地缓存重新导出，不发出任何平台请求；
				# 导出失败会抛 CrawlExportError，不再返回假成功。
				jobs = [item["payload"] for item in self._cache.list_crawl_jobs(run_id)]
				# 并发 clean --privacy 可能已经删除断点：导出前后都重读 crawl_runs。
				# 行已消失时不再写出隐私产物；导出期间被清理时删掉刚重建的文件，
				# 不能复活已清理的数据（Issue #2241；clean 先删行再删文件，与本
				# 检查同一时序契约）。
				if self._cache.get_crawl_run(run_id) is None:
					raise KeyError(run_id)
				write_run_outputs(output_dir, jobs)
				if self._cache.get_crawl_run(run_id) is None:
					_remove_run_artifacts(output_dir)
					raise KeyError(run_id)
			return self._completed_outcome(run)
		# 旧 stop 必须在预检之前清掉：预检要探测可选依赖、读取登录态，可能
		# 耗时；这期间到达的新 stop 不能被后面的清理当成上一轮残留一起清掉
		# （与后台恢复同构，Issue #1115 / #2209）。预检失败时还原旧 stop 与
		# 时间戳，保持「失败的调用不改动任务」的既有约定（Issue #1042）；
		# 失败且无旧 stop 时不写库。
		previous_stop = bool(run.get("stop_requested"))
		previous_updated_at = float(run.get("updated_at") or 0.0)
		previous_status = str(run.get("status") or "")
		previous_error = str(run.get("error") or "")
		if clear_stop and previous_stop:
			self._cache.clear_crawl_stop_request(run_id)
		# 标记本身也可能失败（本地 IO）：此时没有写入时间，还原按旧口径处理。
		marked_at: float | None = None
		try:
			# 取得执行权后、慢预检之前先把状态落成 running：预检（依赖探测、读取
			# 登录态）期间到达的 `crawl stop` 不能被终态拒绝，否则停止请求丢失、
			# 恢复照常发出平台请求（Issue #2240）。预检失败时在下方还原原状态 /
			# 错误 / 时间戳，保持「失败的调用不改动任务」。
			marked_at = self._cache.mark_crawl_run_active(run_id, "running")
			# Hook 目录是本地状态：resume 没有 --hook-dir 参数可以修复，必须在
			# 打开浏览器之前用与 crawl run/start 相同的清单 / 摘要校验拒绝坏目录
			# （Issue #1408）。已完成的 run 在上面直接返回，不因目录后来被删而失败。
			try:
				verify_hook_profile(settings.hook_profile, settings.hook_dir)
			except ValueError as exc:
				# 恢复路径没有参数可改：按确定性本地状态失败上报，不能落进命令层
				# 的「修正 crawl 配置 / crawl configure --help」通用分支（Issue #1460）。
				raise CrawlHookDirInvalid(str(exc), hook_dir=settings.hook_dir) from exc
			if self._preflight is not None:
				self._preflight()
			self._check_risk_lock()
			self._cache.update_crawl_run_params(run_id, settings.as_dict())
		except BaseException as original:
			# 还原本身也可能失败（磁盘满 / 库锁冲突）：不能让还原异常顶替原始
			# 预检错误，否则操作者看不到真正原因，run 还停在 running。原始异常
			# 类型与文案保持不变，还原失败以 note 附加（Issue #2562）。
			restore_failures: list[str] = []
			if clear_stop and previous_stop:
				# 预检 / 参数落库失败：放回清除前的 stop，失败的调用不改动任务。
				try:
					self._cache.restore_crawl_stop_request(run_id, updated_at=previous_updated_at)
				except Exception as exc:
					restore_failures.append(f"stop 标记还原失败（{type(exc).__name__}: {exc}）")
			try:
				self._cache.restore_crawl_run_state(
					run_id,
					status=previous_status,
					error=previous_error,
					updated_at=previous_updated_at,
					marked_at=marked_at,
				)
			except Exception as exc:
				restore_failures.append(f"运行状态还原失败（{type(exc).__name__}: {exc}）")
			if restore_failures:
				original.add_note(
					"恢复失败后的状态还原未完成：" + "；".join(restore_failures)
					+ "。先核对 crawl status，再决定是否手工修正断点"
				)
			raise
		return self._run(
			run_id,
			settings,
			next_page=int(run["next_page"]),
			output_dir=Path(str(run["output_dir"])),
			list_finished=bool(run["list_finished"]),
		)

	def _completed_outcome(self, run: dict[str, Any]) -> CrawlOutcome:
		output_dir = Path(str(run["output_dir"]))
		rows = self._cache.list_crawl_jobs(str(run["run_id"]))
		return CrawlOutcome(
			run_id=str(run["run_id"]),
			status="completed",
			next_page=int(run["next_page"]),
			pages_completed=max(0, int(run["next_page"]) - 1),
			jobs_seen=len(rows),
			detail_checks=0,
			output_paths=_output_paths(output_dir),
			hooks=_hook_injections(run.get("hook_results", []), run_id=str(run["run_id"])),
			requests_attempted=int(run["requests_attempted"]),
			detail_requests_attempted=int(run["detail_requests_attempted"]),
			elapsed_seconds=int(run["elapsed_seconds"]),
			error=str(run.get("error", "")),
			data_dir=self._data_dir,
		)

	def _run(
		self,
		run_id: str,
		settings: CrawlSettings,
		*,
		next_page: int,
		output_dir: Path,
		list_finished: bool = False,
	) -> CrawlOutcome:
		# 预检 / 构造阶段也必须在 try 内（Issue #2466）：transport_factory 抛确定性
		# 失败或命中风控锁时，只有进入 finally 才会写回终态；留在 try 外会把
		# `_resume_locked` 已标记的 running 永久留在断点里、清空上一轮错误，并让
		# `crawl stop` 假成功（叠加 Issue #2465）。
		transport: CrawlTransport | None = None
		run_budget: CrawlRunBudget | None = None
		# 构造阶段失败时 run_budget 还没建好：outcome 改用断点里上一次的预算
		# 计数（本轮 finally 也无从写回），不能让 AttributeError 盖住原始失败。
		baseline_requests = 0
		baseline_details = 0
		baseline_elapsed = 0
		hooks: tuple[HookInjection, ...] = ()
		# 只有 transport.open() 返回（或 HookRegistrationError 带回了注入结果）
		# 之后 hook_results 才可信；否则 finally 必须传 None 保留断点里的上一份
		# 记录，不能把打开浏览器前的失败清成空列表（Issue #1983）。
		hooks_recorded = False
		detail_checks = 0
		failed_detail_keys: set[str] = set()
		status = "completed"
		error = ""
		# 成功路径在 completed 落库前导出并覆盖这里；异常路径保持空，错误信封
		# 不会指向不存在的产物（Issue #1238）。
		paths: dict[str, str] = {}
		risk_lock: dict[str, Any] | None = None
		current_page = next_page
		# 风控/待人工处理时保留采集 Chrome，便于用户在窗口内登录或过验证。
		keep_browser = False
		try:
			previous = self._cache.get_crawl_run(run_id)
			if previous is None:
				raise KeyError(run_id)
			baseline_requests = int(previous["requests_attempted"])
			baseline_details = int(previous["detail_requests_attempted"])
			baseline_elapsed = int(previous["elapsed_seconds"])
			self._check_risk_lock()
			transport = self._transport_factory(settings)
			budget = self._budget_factory(self._cache)
			active_budget = CrawlRunBudget(
				settings,
				requests_attempted=baseline_requests,
				detail_requests_attempted=baseline_details,
				elapsed_seconds=baseline_elapsed,
				on_change=lambda current: self._cache.update_crawl_run_budget(
					run_id,
					requests_attempted=current.requests_attempted,
					detail_requests_attempted=current.detail_requests_attempted,
					elapsed_seconds=current.elapsed_seconds,
				),
			)
			run_budget = active_budget

			def check_attempt() -> None:
				self._raise_if_stop_requested(run_id)
				active_budget.check_time()

			bind_check = getattr(budget, "bind_check", None)
			if callable(bind_check):
				bind_check(check_attempt)
			bind_attempts = getattr(transport, "bind_attempts", None)
			if callable(bind_attempts):
				bind_attempts(
					lambda kind: self._attempt(run_id, budget, active_budget, kind), self._request_budget.cooldown
				)
			# 恢复 stopped 等终态 run 时，状态要在打开浏览器之前落成 running：
			# 否则浏览器启动与 seed 导航期间 `crawl stop` 会按终态被拒，而采集
			# 照常继续（Issue #1771）。放在 try 内，任何失败都由 finally 写回终态。
			self._cache.mark_crawl_run_active(run_id, "running")
			self._raise_if_stop_requested(run_id)
			if not list_finished and 1 < next_page <= settings.pages:
				# 深层断点的导航本身就可能超过剩余预算，先检查再打开浏览器（Issue #2463）。
				run_budget.check_list_request_capacity(transport, next_page, before_open=True)
			hooks = tuple(transport.open())
			hooks_recorded = True
			self._raise_if_stop_requested(run_id)
			self._cache.update_crawl_run(
				run_id,
				status="running",
				next_page=current_page,
				list_finished=list_finished,
				hook_results=_hook_metadata(hooks),
			)
			detail_checks += self._complete_pending_details(
				run_id, settings, transport, budget, run_budget, failed_keys=failed_detail_keys
			)
			while not list_finished and current_page <= settings.pages:
				self._raise_if_stop_requested(run_id)
				body = self._with_retry(
					lambda: self._fetch_list(run_id, transport, budget, run_budget, settings, current_page),
					max_retries=settings.max_retries,
				)
				# _fetch_list 已在有限重试范围内校验结构；未取得列表不能推进断点。
				jobs_data = body["zpData"]
				jobs = jobs_data["jobList"]
				for rank, raw in enumerate(jobs, 1):
					if not isinstance(raw, dict):
						continue
					job_key, row = _normalize_job(raw, settings.query, current_page, rank)
					existing = self._cache.get_crawl_job(run_id, job_key)
					if existing is not None:
						# 游标尚未推进时重抓同一页，需补全待处理行缺失的详情标识；
						# 保留原始列表证据和已经完成的详情（Issue #2574）。
						previous = existing["payload"]
						if (
							not existing["detail_done"]
							and row["security_id"]
							and not _detail_id(previous.get("security_id"))
						):
							previous["security_id"] = row["security_id"]
							self._put_crawl_job(run_id, job_key, existing["page_no"], previous, detail_done=False)
						continue
					# 原始列表项必须先落库：详情的风险码或第二次超时不会丢失待补队列。
					self._put_crawl_job(run_id, job_key, current_page, row, detail_done=False)

				current_page += 1
				# 只有平台显式返回布尔 false 才算列表结束。`not bool(None)` 为真，
				# 原写法会把显式 null 当成「没有更多」，第一页就停止且 run 报
				# completed；缺失 / null / 类型漂移一律继续翻页到页数上限
				# （Issue #1345）。
				list_finished = jobs_data.get("hasMore") is False
				self._cache.update_crawl_run(
					run_id,
					status="running",
					next_page=current_page,
					list_finished=list_finished,
					hook_results=_hook_metadata(hooks),
				)
				detail_checks += self._complete_pending_details(
					run_id, settings, transport, budget, run_budget, failed_keys=failed_detail_keys
				)
				write_run_outputs(output_dir, [item["payload"] for item in self._cache.list_crawl_jobs(run_id)])
				_discard_artifacts_if_run_removed(self._cache, run_id, output_dir)
				if list_finished:
					break
			# 最后一页的导出必须在 completed 落库之前（Issue #1238）：第二次 resume
			# 时页循环不执行，若导出留在 finally 之后，completed 会先写进数据库，
			# 导出失败后再也无法重试，后续 resume 会报成功但产物不存在。
			# 最后一页之后断点可能已被清理：导出前再确认一次，不能重建已删除的
			# 产物目录并把 run 报成 completed（Issue #2208）。
			self._raise_if_stop_requested(run_id)
			rows = [item["payload"] for item in self._cache.list_crawl_jobs(run_id)]
			paths = write_run_outputs(output_dir, rows)
			_discard_artifacts_if_run_removed(self._cache, run_id, output_dir)
		except HookRegistrationError as exc:
			# Hook 注入失败是确定性本地失败：保存断点后原样抛出，由入口按
			# LOCAL_IO_ERROR 回报并给出具体目录。落成普通 stopped 会让调用方拿到
			# ok:true 和会无限重复的 resume 指引（Issue #1408）。
			hooks = exc.injections
			hooks_recorded = True
			status = "stopped"
			error = str(exc)
			raise
		except CrawlRiskError as exc:
			status = "risk_stopped"
			error = str(exc)
			keep_browser = True
			risk_lock = self._record_risk_lock(exc)
		except PlatformRiskError as exc:
			status = "risk_stopped"
			# 平台响应原文可能带临时凭据，运行结果里只留登记表文案。
			error = risk_error_message(exc.code)
			keep_browser = True
			risk_lock = exc.details.get("risk_lock")
		except CrawlLimitExceeded as exc:
			status = "budget_stopped"
			error = str(exc)
		except RequestBudgetExceeded as exc:
			# REQUEST_BUDGET_EXHAUSTED 是本地共享请求预算的等待超时（另一个进程持有
			# 租约、冷却未结束或等不到下一个请求间隔），run 自己的预算并没有耗尽：
			# 落成 budget_stopped 会让入口提示「新建任务」，而等待后用 resume 就能
			# 从断点继续（Issue #1556）。run 预算耗尽走 CrawlLimitExceeded。
			status = (
				"timeout_stopped"
				if exc.code == "WORKFLOW_TIMEOUT"
				else "workflow_stopped"
				if exc.code == "WORKFLOW_STOPPED"
				else "throttle_stopped"
			)
			error = str(exc)
		except CrawlTimeoutRequested as exc:
			status = "timeout_stopped"
			error = str(exc)
		except CrawlWorkflowStopRequested as exc:
			status = "workflow_stopped"
			error = str(exc)
		except CrawlStopRequested as exc:
			status = "stopped"
			error = str(exc)
		except KeyError:
			# 初始读取或导出后重读断点发现行已被 clean --privacy 删除：本次
			# 没有产物（或产物已撤销），按 run 不存在上抛，命令层回
			# CRAWL_RUN_NOT_FOUND（Issue #2323、#2466）。
			status = "stopped"
			error = "crawl run was removed before this attempt finished"
			raise
		except (CacheBusyError, sqlite3.OperationalError) as exc:
			# 缓存层写锁冲突：`CacheBusyError` 是 `_GuardedConnection` 归一后的形态，
			# 原始 `sqlite3.OperationalError('database is locked')` 也可能直接从
			# 取数路径抛出（`is_cache_busy_error` 的文本形态）。两者都必须保存断点
			# 后原样上抛，由入口落成可重试的 CACHE_BUSY；落成普通 stopped 会让调用
			# 方拿到 ok:true 与可无限重复的 resume 指引（Issue #1477）。非锁的
			# OperationalError（坏页 / 只读库）同样原样上抛，按既有契约分类
			# （Issue #1350、#1467）。
			status = "stopped"
			error = str(exc)
			raise
		except (
			CrawlRuntimeUnavailable,
			CrawlExportError,
			CrawlProfileBusy,
			CrawlProfilePathInvalid,
			CrawlSessionAuthError,
			CacheRecordInvalid,
			CacheDatabaseInvalid,
			sqlite3.DatabaseError,
		) as exc:
			# 可选依赖缺失、导出失败、配置目录被另一个采集 Chrome 占用、配置目录
			# 路径不可用、本地登录态无法注入采集浏览器、本地记录损坏与缓存库损坏
			# （含取数阶段才暴露的坏页）都是确定性失败：保存断点后原样抛出，由入口
			# 按 CRAWL_UNAVAILABLE / LOCAL_IO_ERROR / CRAWL_PROFILE_BUSY /
			# AUTH_REQUIRED / CACHE_RECORD_INVALID / CACHE_DATABASE_INVALID 回报。
			# 落成普通的 stopped 会让调用方拿到成功信封与恢复指引，而重跑仍会撞上
			# 同一个占用者、坏路径、坏目录或坏库（Issue #1050、#1135、#1212、#1266、
			# #1290、#1350）。CrawlSessionAuthError 尤其不能进入风控分支：本地
			# Cookie 注入失败不是平台风控，写风险锁会误导用户去 clean（Issue #1405）。
			status = "stopped"
			error = str(exc)
			raise
		except Exception as exc:
			# transport 的控制检查会透传 wizard 的 ambient 信号
			# （WorkflowActionError 的 WORKFLOW_STOPPED / WORKFLOW_TIMEOUT）；按错误码
			# 归入 crawl 自己的停止语义。落成普通 stopped 会让调用方看到可重试的
			# NETWORK_ERROR，甚至给出会清掉 stop 标记的恢复命令（Issue #1238）。
			code = getattr(exc, "code", None)
			if code == "WORKFLOW_TIMEOUT":
				status = "timeout_stopped"
			elif code == "WORKFLOW_STOPPED":
				status = "workflow_stopped"
			else:
				status = "stopped"
			error = str(exc)
		except BaseException:
			# SystemExit 等退出信号也会进入 finally：先保存中断断点，不能把未完成
			# 的任务按默认状态记为 completed。原信号继续向外传播（Issue #1024）。
			status = "stopped"
			error = "execution interrupted; verify any unfinished operation before explicitly resuming"
			raise
		finally:
			# 三步各自独立兜底：budget 写失败不能让终态落库与浏览器关闭被跳过，
			# 否则 run 永远停在 running、本次错误丢失，采集 Chrome 继续占用共享
			# profile（Issue #1277）。
			cleanup_errors: list[BaseException] = []
			if run_budget is not None:
				try:
					# Save progress before cleanup: a detached browser can fail to close.
					self._cache.update_crawl_run_budget(
						run_id,
						requests_attempted=run_budget.requests_attempted,
						detail_requests_attempted=run_budget.detail_requests_attempted,
						elapsed_seconds=run_budget.elapsed_seconds,
					)
				except Exception as exc:
					cleanup_errors.append(exc)
			try:
				self._cache.update_crawl_run(
					run_id,
					status=status,
					next_page=current_page,
					error=error,
					list_finished=list_finished,
					hook_results=_hook_metadata(hooks) if hooks_recorded else None,
				)
			except Exception as exc:
				cleanup_errors.append(exc)
			close = getattr(transport, "close", None)
			if callable(close):
				try:
					try:
						close(quit_browser=not keep_browser)
					except TypeError:
						# 旧 transport / 测试假对象可能不接受关键字参数。
						close()
				except Exception as close_error:
					if status == "completed":
						status = "stopped"
					error = f"{error}; browser cleanup failed ({type(close_error).__name__})".lstrip("; ")
					try:
						self._cache.update_crawl_run(run_id, status=status, next_page=current_page, error=error)
					except Exception as exc:
						cleanup_errors.append(exc)
			if cleanup_errors and sys.exc_info()[0] is None:
				# 正常路径上本地清理失败必须暴露（命令层报 CACHE_BUSY / LOCAL_IO_ERROR），
				# 但不能覆盖正在传播的原始异常（风控 / 中断 / 停止）。
				raise cleanup_errors[0]

		rows = [item["payload"] for item in self._cache.list_crawl_jobs(run_id)]
		return CrawlOutcome(
			run_id=run_id,
			status=status,
			next_page=current_page,
			pages_completed=max(0, current_page - 1),
			jobs_seen=len(rows),
			detail_checks=detail_checks,
			output_paths=paths,
			hooks=hooks,
			error=error,
			requests_attempted=run_budget.requests_attempted if run_budget is not None else baseline_requests,
			detail_requests_attempted=(
				run_budget.detail_requests_attempted if run_budget is not None else baseline_details
			),
			elapsed_seconds=run_budget.elapsed_seconds if run_budget is not None else baseline_elapsed,
			risk_lock=risk_lock,
			data_dir=self._data_dir,
		)

	def _check_risk_lock(self) -> None:
		if self._unpersisted_risk_lock is not None:
			raise PlatformRiskError.from_lock(self._unpersisted_risk_lock)
		cdp_risk_lock.raise_if_locked(self._data_dir)
		self._request_budget.check_risk()

	def _record_risk_lock(self, error: CrawlRiskError) -> dict[str, Any]:
		lock = cdp_risk_lock.RiskLock(
			stoken_sha256=None,
			locked_at=time.time(),
			reason=error.reason,
			platform_code=error.platform_code,
		)
		persisted = True
		try:
			self._request_budget.record_risk(reason=lock.reason, platform_code=lock.platform_code)
			self._unpersisted_risk_lock = None
		except OSError:
			self._unpersisted_risk_lock = lock
			persisted = False
		return {**PlatformRiskError.from_lock(lock).details["risk_lock"], "persisted": persisted}

	@contextmanager
	def _attempt(self, run_id: str, budget: CrawlBudget, run_budget: CrawlRunBudget, kind: str) -> Iterator[None]:
		category = "detail" if kind.startswith("detail") else "list"

		def check() -> None:
			self._raise_if_stop_requested(run_id)
			run_budget.check_time()

		with request_control(check=check):
			with self._request_budget.attempt(
				f"crawl-{kind}",
				check=check,
				before_wait=lambda: budget.wait(category),
				claim=lambda: run_budget.claim_request(category),
			):
				try:
					yield
				except CrawlRiskError as error:
					# Persist before releasing the shared lease; another process sees the lock.
					self._record_risk_lock(error)
					raise

	def _complete_pending_details(
		self,
		run_id: str,
		settings: CrawlSettings,
		transport: CrawlTransport,
		budget: CrawlBudget,
		run_budget: CrawlRunBudget,
		*,
		failed_keys: set[str] | None = None,
	) -> int:
		if not settings.with_detail:
			return 0
		checks = 0
		for item in self._cache.list_crawl_jobs(run_id):
			self._raise_if_stop_requested(run_id)
			if item["detail_done"]:
				continue
			if failed_keys is not None and item["job_key"] in failed_keys:
				# 本轮已经失败过的条目不再每翻一页重试，避免持续消耗详情预算；
				# 下次 resume 会重新尝试（Issue #1981）。
				continue
			checks += 1
			row = item["payload"]
			# 显式 null / 容器类型按缺失处理：str(None) 会得到 "None"，
			# 让详情缓存按这个伪主键跨职位共享，后一个职位直接采用前一个
			# 职位的描述并跳过自己的详情请求（Issue #2642）。
			job_id = safe_text(row.get("job_id") or "")
			cached = self._cache.get_job_desc(job_id)
			if cached:
				row["post_description"] = cached
				row["detail_status"] = "cached"
			else:
				security_id = _detail_id(row.get("security_id"))
				if not security_id:
					# 列表项没有 securityId 时无法请求详情；保留 pending 让 resume
					# 与 `crawl results --detail-status pending` 如实反映缺口，
					# 不能伪造空卡片并标记完成（Issue #1584）。
					row["detail_status"] = "missing-security-id"
					self._put_crawl_job(run_id, item["job_key"], int(item["page_no"]), row, detail_done=False)
					continue

				def fetch_detail() -> dict[str, Any]:
					# 结构校验与请求共用有限重试；失败时条目仍保留在待补队列。
					return _unwrap_card(self._fetch_detail(run_id, transport, budget, run_budget, security_id))

				try:
					card = self._with_retry(fetch_detail, max_retries=settings.max_retries)
				except Exception as exc:
					# 本地缓存锁冲突 / 断点损坏与 _with_retry 同口径原样上抛，
					# 不能降级成单条 detail-error 后把整轮报成 completed
					# （Issue #2324、#2351）。
					if is_request_stop(exc) or isinstance(
						exc,
						(
							PlatformRiskError,
							CrawlRiskError,
							CrawlRuntimeUnavailable,
							CrawlSessionAuthError,
							CacheBusyError,
							CacheRecordInvalid,
							sqlite3.DatabaseError,
						),
					):
						raise
					# 平台对已下架 / 无权限职位返回空 jobCard 或单条业务错误码很常见：
					# 单个条目失败不能让列表翻页与导出永久卡死（Issue #1981）。保留
					# 待补状态继续处理其余条目，下次 resume 再试。
					row["detail_status"] = "detail-error"
					self._put_crawl_job(run_id, item["job_key"], int(item["page_no"]), row, detail_done=False)
					if failed_keys is not None:
						failed_keys.add(item["job_key"])
					continue
				row.update(
					{
						"post_description": field_text(card, "postDescription"),
						"address": field_text(card, "address"),
						"boss_name": field_text(card, "bossName"),
						"boss_title": field_text(card, "bossTitle"),
						"detail_status": "fetched",
					}
				)
				if job_id:
					self._cache.put_job_desc(job_id, str(row["post_description"]))
			self._put_crawl_job(run_id, item["job_key"], int(item["page_no"]), row, detail_done=True)
		return checks

	@staticmethod
	def _with_retry(action: Callable[[], dict[str, Any]], *, max_retries: int) -> dict[str, Any]:
		last_error: Exception | None = None
		for _ in range(max_retries + 1):
			try:
				return action()
			except (
				PlatformRiskError,
				CrawlRiskError,
				CrawlLimitExceeded,
				CrawlStopRequested,
				CacheBusyError,
				CacheRecordInvalid,
				sqlite3.DatabaseError,
			):
				# 只读目录 / 磁盘满等本地数据库故障重试同一次请求不会自愈；保留原始
				# `sqlite3.DatabaseError` 让上层按 LOCAL_IO_ERROR 收口（Issue #1467）。
				# 写锁冲突与断点损坏同样不重试：每次重跑都会重新 claim，虚增预算并
				# 重复占用节流槽（Issue #2324）。
				raise
			except Exception as exc:
				if is_request_stop(exc):
					raise
				last_error = exc
		if last_error is None:
			raise RuntimeError("crawl operation failed")
		raise last_error

	def _fetch_list(
		self,
		run_id: str,
		transport: CrawlTransport,
		budget: CrawlBudget,
		run_budget: CrawlRunBudget,
		settings: CrawlSettings,
		page_no: int,
	) -> dict[str, Any]:
		self._raise_if_stop_requested(run_id)
		if page_no > 1:
			# 详情补取和前一次导航失败都可能改变可用预算。
			run_budget.check_list_request_capacity(transport, page_no)
		window = (
			nullcontext()
			if callable(getattr(transport, "bind_attempts", None))
			else self._attempt(run_id, budget, run_budget, "list")
		)
		with window:
			payload = transport.fetch_page(settings.query, settings.city_code, page_no)
			if normalize_risk_code(payload.get("code")) is not None:
				raise CrawlRiskError.from_response(payload)
		self._raise_if_stop_requested(run_id)
		code = payload.get("code")
		if code is not None and not is_platform_success_code(code):
			raise RuntimeError(f"第 {page_no} 页接口错误 code={code}: {payload.get('message', '')}")
		data = payload.get("zpData")
		if not isinstance(data, dict) or not isinstance(data.get("jobList"), list):
			raise RuntimeError(f"第 {page_no} 页响应缺少有效的 zpData.jobList 列表")
		return payload

	def _fetch_detail(
		self,
		run_id: str,
		transport: CrawlTransport,
		budget: CrawlBudget,
		run_budget: CrawlRunBudget,
		security_id: str,
	) -> dict[str, Any]:
		self._raise_if_stop_requested(run_id)
		window = (
			nullcontext()
			if callable(getattr(transport, "bind_attempts", None))
			else self._attempt(run_id, budget, run_budget, "detail")
		)
		with window:
			payload = transport.fetch_detail(security_id)
			if normalize_risk_code(payload.get("code")) is not None:
				raise CrawlRiskError.from_response(payload)
		self._raise_if_stop_requested(run_id)
		code = payload.get("code")
		if code is not None and not is_platform_success_code(code):
			raise RuntimeError(f"职位详情接口错误 code={code}: {payload.get('message', '')}")
		return payload

	def _raise_if_stop_requested(self, run_id: str) -> None:
		self._check_risk_lock()
		if self._control_probe is not None:
			signal = self._control_probe()
			if signal == "timeout":
				raise CrawlTimeoutRequested("workflow timeout requested")
			if signal == "stop":
				self._cache.request_crawl_stop(run_id)
				raise CrawlWorkflowStopRequested("workflow stop requested")
		run = self._cache.get_crawl_run(run_id)
		if run is None:
			# 取得执行权后断点行消失：`clean --privacy` 会删除 crawl_runs /
			# crawl_jobs。继续采集会复活已清理的职位行、重建导出目录，而且
			# `crawl stop` 已按 CRAWL_RUN_NOT_FOUND 无法再停止它（Issue #2208）。
			# 控制路径按「已停止」fail-closed，不再向平台发请求。
			raise CrawlStopRequested("采集断点已被删除（可能执行了 clean --privacy），停止本次采集")
		if run["stop_requested"]:
			raise CrawlStopRequested("stop requested")

	def _put_crawl_job(
		self,
		run_id: str,
		job_key: str,
		page_no: int,
		payload: dict[str, Any],
		*,
		detail_done: bool,
	) -> None:
		"""写入职位行；断点已消失时按停止结算，不复活已清理的数据（Issue #2208）。"""
		if not self._cache.put_crawl_job(
			run_id, job_key, page_no, payload, detail_done=detail_done, require_existing_run=True
		):
			raise CrawlStopRequested("采集断点已被删除（可能执行了 clean --privacy），停止本次采集")

	@staticmethod
	def _validate_settings(settings: CrawlSettings) -> None:
		if not settings.query.strip():
			# 各创建入口都在前面校验了关键词，这里是 service 层的最后一道防线：
			# 空白关键词等于不筛选，会按城市默认列表抓取并放大风控面（Issue #1582）。
			raise ValueError("搜索关键词不能为空")
		if settings.pages < 1:
			raise ValueError("pages 必须是正整数")
		if settings.max_requests < 1:
			raise ValueError("max_requests 必须是正整数")
		if settings.max_details < 1:
			raise ValueError("max_details 必须是正整数")
		if settings.max_seconds < 1:
			raise ValueError("max_seconds 必须是正整数")
		if settings.max_retries < 0:
			raise ValueError("max_retries 不能为负数")

	def validate_resume_settings(self, settings: CrawlSettings) -> None:
		"""后台父进程的本地校验：与执行器同口径拒绝坏设置与坏 Hook 目录（Issue #2242）。

		worker 的错误信封写进空设备：hook 目录损坏、关键词为空白这类确定性本地
		失败必须在父进程返回 queued 之前报出来，不能先报成功再让任务停在无人
		执行的状态。
		"""
		self._validate_settings(settings)
		try:
			verify_hook_profile(settings.hook_profile, settings.hook_dir)
		except ValueError as exc:
			raise CrawlHookDirInvalid(str(exc), hook_dir=settings.hook_dir) from exc

	def validate_resume_basics(self, settings: CrawlSettings) -> None:
		"""completed 快速路径只校验设置语义，与前台 resume 的校验顺序一致。

		前台在 ``has_resume_work`` 之前就执行 ``_validate_settings``；后台无需启动
		worker 的 completed 路径也要先拒绝空白关键词等确定性坏设置，否则同一个
		断点会因入口形式不同得到相反结论（Issue #2402）。Hook 目录探测留到真正
		启动 worker 前，completed 路径不额外收紧。
		"""
		self._validate_settings(settings)


def _detail_id(value: Any) -> str:
	"""详情标识必须是可发送的非空文本，不能把容器或空值变成请求参数。"""
	if not isinstance(value, str) or not value.strip():
		return ""
	try:
		value.encode("utf-8")
	except UnicodeEncodeError:
		return ""
	return value


def _normalize_job(raw: dict[str, Any], query: str, page_no: int, rank: int) -> tuple[str, dict[str, Any]]:
	# 不能把对象等异常值变成稳定身份，否则不同职位可能共用去重键（Issue #2574）。
	raw_id = raw.get("encryptJobId")
	if isinstance(raw_id, str):
		job_id = raw_id if raw_id.strip() else ""
	elif type(raw_id) is int and raw_id != 0:
		job_id = safe_text(raw_id)
	else:
		job_id = ""
	# 平台 JSON 可能出现孤立代理项；SQLite 绑定的去重键需保留可编码的形式
	# （Issue #2350）。详情标识用于请求，不得发送改写过的值。
	job_id = sanitize_surrogates(job_id)
	security_id = _detail_id(raw.get("securityId"))
	# securityId 每次搜索请求都会轮换（operations.import_shortlist 同样不拿它做
	# 跨 run 去重）：页内崩溃后 resume 重抓同一页会拿到新值，用它当身份会让同一
	# 职位以新键再插一行。无稳定 job_id 时按「页 + 名次 + 标题」兜底，它们在
	# 同一页的重复抓取之间保持不变（Issue #2029）。
	job_key = job_id or f"{page_no}:{rank}:{sanitize_surrogates(field_text(raw, 'jobName'))}"
	labels = raw.get("jobLabels", [])
	benefits = raw.get("welfareList", [])
	return job_key, {
		"query": query,
		"page": page_no,
		"rank": rank,
		"job_id": job_id,
		"security_id": security_id,
		"lid": str(raw.get("lid") or ""),
		"title": field_text(raw, "jobName"),
		"salary": field_text(raw, "salaryDesc"),
		"city": field_text(raw, "cityName"),
		"district": field_text(raw, "areaDistrict"),
		"business_district": field_text(raw, "businessDistrict"),
		"company": field_text(raw, "brandName"),
		"company_scale": field_text(raw, "brandScaleName"),
		"industry": field_text(raw, "brandIndustry"),
		"education": field_text(raw, "jobDegree"),
		"experience": field_text(raw, "jobExperience"),
		"labels": "、".join(str(item) for item in labels) if isinstance(labels, list) else str(labels or ""),
		"benefits": "、".join(str(item) for item in benefits) if isinstance(benefits, list) else str(benefits or ""),
		"post_description": "",
		"address": "",
		"boss_name": field_text(raw, "bossName"),
		"boss_title": field_text(raw, "bossTitle"),
		"detail_status": "not_requested",
	}


def _unwrap_card(payload: dict[str, Any]) -> dict[str, Any]:
	data = payload.get("zpData")
	card = data.get("jobCard") if isinstance(data, dict) else None
	if not isinstance(card, dict):
		raise RuntimeError("职位详情响应缺少有效的 zpData.jobCard 对象")
	return card


def _hook_metadata(hooks: tuple[HookInjection, ...]) -> list[dict[str, Any]]:
	return [
		{"name": item.name, "success": item.success, "sha256": item.sha256, "reason": item.reason} for item in hooks
	]


def _hook_injections(raw: Any, *, run_id: str = "") -> tuple[HookInjection, ...]:
	if not isinstance(raw, list):
		return ()
	injections: list[HookInjection] = []
	for item in raw:
		if not isinstance(item, dict):
			continue
		success = item.get("success", False)
		# bool("false") / bool(0) 都会把失败的注入显示成成功：断点里的 success
		# 只认真正的布尔值，类型漂移按记录损坏处理（Issue #1484）。
		if not isinstance(success, bool):
			raise CacheRecordInvalid(
				"crawl_run", run_id, "hook_results", f"success 期望布尔值，实际是 {type(success).__name__}"
			)
		injections.append(
			HookInjection(
				name=str(item.get("name", "")),
				success=success,
				sha256=str(item.get("sha256", "")),
				reason=str(item.get("reason", "")),
			)
		)
	return tuple(injections)


def _output_paths(output_dir: Path) -> dict[str, str]:
	return {extension: str(output_dir / f"jobs.{extension}") for extension in ("json", "csv", "xlsx")}


def _discard_artifacts_if_run_removed(cache: CacheStore, run_id: str, output_dir: Path) -> None:
	"""导出后重读断点：并发 ``clean --privacy`` 已删行时删掉刚写出的产物。

	clean 先提交删行、再删产物；执行器可能在删行前读完 payload，序列化
	CSV/XLSX 本身耗时，窗口不小。行消失说明清理已生效，不能把隐私数据重新
	落盘（Issue #2241、#2323）。
	"""
	if cache.get_crawl_run(run_id) is None:
		_remove_run_artifacts(output_dir)
		raise KeyError(run_id)


def _remove_run_artifacts(output_dir: Path) -> None:
	"""删掉三份确定性产物；缺失 / 无法删除的文件忽略（Issue #2241）。

	清理失败不能把「断点已被清理」的确定性错误盖成 INTERNAL_ERROR：调用方
	需要的结论是任务已不存在，而不是某个已失效文件删不掉。
	"""
	for extension in ("json", "csv", "xlsx"):
		try:
			(output_dir / f"jobs.{extension}").unlink(missing_ok=True)
		except (OSError, ValueError):
			pass


def _artifacts_present(output_dir: Path) -> bool:
	"""三份产物是否都还是普通文件；任何 stat 错误按缺失处理，交给导出路径报确定性错误。"""
	for extension in ("json", "csv", "xlsx"):
		try:
			mode = (output_dir / f"jobs.{extension}").lstat().st_mode
		except (OSError, ValueError):
			# ValueError 来自路径里的 NUL 等控制字符；读取边界已经拒绝这类
			# 断点（Issue #2194），这里再兜一层，避免字符错误逃成参数错误。
			return False
		if not stat.S_ISREG(mode):
			return False
	return True
