"""Local, bounded request coordination across CLI processes and transports.

The OS lease covers one active operation, including response risk classification.
SQLite holds only scheduling metadata; no request parameters or credentials.
Neither a cooldown expiry nor a process exit clears a platform risk lock.
"""

from __future__ import annotations

import contextvars
import math
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from boss_agent_cli.services.file_lock import try_lock_exclusive

if TYPE_CHECKING:
	from boss_agent_cli.api.cdp_risk_lock import RiskLock


def _tolerated_timestamp(raw: Any, *, default: float = 0.0) -> float:
	"""节流元数据的容错读取：漂移值按 0 处理。

	``request_state`` / ``request_attempts`` 是本地调度缓存，坏值不能让所有平台
	请求抛 ``TypeError``（会被兜成不可重试的 ``INTERNAL_ERROR``），也不能让等待
	时间无限延长（Issue #1212）。有限的远未来值同样超出合法预留窗口，由
	``attempt()`` 按 ``now + interval + skew`` 判定并写回（Issue #1422）。
	"""
	try:
		parsed = float(raw)
	except (TypeError, ValueError):
		return default
	return parsed if math.isfinite(parsed) else default


#: 本地调度元数据允许的超前窗口（Issue #1422）。
#:
#: 写入方最多把 ``next_at`` 推到 ``now + interval``；读写之间只该有很小的
#: 时钟与调度偏移。更远的「未来」值只能是漂移：它会让每条命令都撞
#: ``REQUEST_BUDGET_EXHAUSTED``，而拒绝等待的路径不会写回，预算从此无法自愈。
_FUTURE_SKEW_SECONDS = 60.0

#: 服务器 ``Retry-After`` 冷却的本地上限（Issue #1422）。
#:
#: 冷却可以合法地长于请求间隔（平台限流反馈），所以不能套用上面的窗口；
#: 但也没有平台会要求等待超过一小时。超过的本地值按漂移截断并写回，恢复
#: 指引沿用 ``REQUEST_BUDGET_EXHAUSTED`` 的契约（等待冷却后显式恢复）。
_MAX_COOLDOWN_SECONDS = 60.0 * 60.0


class RequestBudgetExceeded(RuntimeError):
	"""An unsent operation cannot obtain a slot within its remaining time."""

	def __init__(
		self, message: str, *, code: str = "REQUEST_BUDGET_EXHAUSTED", retry_after: float | None = None
	) -> None:
		self.code = code
		self.details: dict[str, object] = {"request_sent": False}
		if retry_after is not None:
			self.details["retry_after_seconds"] = max(0.0, retry_after)
		super().__init__(message)


@dataclass(frozen=True)
class RequestControl:
	check: Callable[[], None] | None = None
	deadline: float | None = None


_CONTROL: contextvars.ContextVar[RequestControl] = contextvars.ContextVar(
	"boss_request_control", default=RequestControl()
)
_LEASES: contextvars.ContextVar[tuple[tuple[int, int, str], ...]] = contextvars.ContextVar(
	"boss_request_leases", default=()
)


@contextmanager
def request_control(*, check: Callable[[], None] | None = None, deadline: float | None = None) -> Iterator[None]:
	"""Bind workflow controls without coupling transports to workflow classes."""
	prior = _CONTROL.get()

	def combined_check() -> None:
		if prior.check is not None:
			prior.check()
		if check is not None:
			check()

	deadlines = [value for value in (prior.deadline, deadline) if value is not None]
	token = _CONTROL.set(RequestControl(combined_check, min(deadlines) if deadlines else None))
	try:
		yield
	finally:
		_CONTROL.reset(token)


def check_request_control(check: Callable[[], None] | None = None) -> None:
	control = _CONTROL.get()
	if check is not None:
		check()
	if control.check is not None:
		control.check()
	if control.deadline is not None and time.monotonic() >= control.deadline:
		raise RequestBudgetExceeded("工作流已超过截止时间，请求未发出", code="WORKFLOW_TIMEOUT")


def request_controls_active() -> bool:
	control = _CONTROL.get()
	return control.check is not None or control.deadline is not None


def cancellable_sleep(
	seconds: float, *, check: Callable[[], None] | None = None, sleeper: Callable[[float], None] | None = None
) -> None:
	"""Sleep in short slices when risk/stop/deadline controls are available."""
	check_request_control(check)
	sleep = sleeper or time.sleep
	control = _CONTROL.get()
	if control.deadline is not None and seconds > max(0.0, control.deadline - time.monotonic()):
		raise RequestBudgetExceeded(
			"等待时间会超出工作流截止时间，请求未发出", code="WORKFLOW_TIMEOUT", retry_after=seconds
		)
	if check is None and control.check is None and control.deadline is None:
		if seconds > 0:
			sleep(seconds)
		return
	remaining = max(0.0, seconds)
	while remaining > 0:
		check_request_control(check)
		slice_seconds = min(0.1, remaining)
		sleep(slice_seconds)
		remaining -= slice_seconds
	check_request_control(check)


def validate_delay(delay: tuple[float, float]) -> None:
	if len(delay) != 2 or not all(math.isfinite(value) for value in delay) or not 0 <= delay[0] <= delay[1]:
		raise ValueError("request delay must be finite and satisfy 0 <= low <= high")


def is_request_stop(error: BaseException) -> bool:
	return (
		isinstance(error, RequestBudgetExceeded)
		or getattr(error, "request_control_stop", False) is True
		or getattr(error, "code", None)
		in {
			"WORKFLOW_STOPPED",
			"WORKFLOW_TIMEOUT",
			"ACCOUNT_RISK",
			"ENVIRONMENT_RISK",
			"ENVIRONMENT_RISK_LOCKED",
		}
	)


def retry_after_seconds(value: object, *, now: float | None = None) -> float | None:
	"""Accept valid delta-seconds or an HTTP date; never execute header data."""
	if not isinstance(value, str) or not value.strip():
		return None
	value = value.strip()
	if value.isascii() and value.isdecimal():
		try:
			seconds = float(value)
		except ValueError:
			return None
		return seconds if math.isfinite(seconds) else None
	try:
		date = parsedate_to_datetime(value)
		if date.tzinfo is None:
			date = date.replace(tzinfo=timezone.utc)
		seconds = date.timestamp() - (time.time() if now is None else now)
	except (TypeError, ValueError, OverflowError):
		return None
	return max(0.0, seconds) if math.isfinite(seconds) else None


def budget_directory(data_dir: Path) -> Path:
	from boss_agent_cli.config import expand_budget_directory, load_config

	value = load_config(data_dir / "config.json").get("request_budget_dir")
	if value is None:
		return data_dir.resolve()
	if not isinstance(value, str) or not value.strip():
		raise ValueError("request_budget_dir must be a nonempty absolute path or null")
	return expand_budget_directory(value).resolve()


def risk_lock_status(data_dir: Path) -> dict[str, Any]:
	"""Report the effective local/group risk without creating coordination files."""
	from boss_agent_cli.api import cdp_risk_lock
	local = cdp_risk_lock.lock_status(data_dir)
	root = budget_directory(data_dir)
	if root == data_dir.resolve():
		return local
	group = cdp_risk_lock.lock_status(root)
	return {**(local if local["locked"] else group), "group_path": str(root), "group_locked": group["locked"]}


class RequestBudget:
	"""One active operation per local budget directory, with a shared ceiling."""

	def __init__(
		self,
		data_dir: Path,
		*,
		interval: float = 1.5,
		max_per_minute: int = 20,
		wait_timeout: float = 30.0,
		root: Path | None = None,
	) -> None:
		if not math.isfinite(interval) or interval < 0:
			raise ValueError("request budget interval must be finite and nonnegative")
		if isinstance(max_per_minute, bool) or not isinstance(max_per_minute, int) or max_per_minute <= 0:
			raise ValueError("request_max_per_minute must be a positive integer")
		if not math.isfinite(wait_timeout) or wait_timeout <= 0:
			raise ValueError("request_wait_timeout must be finite and positive")
		self.data_dir = data_dir.resolve()
		self.root = (root or data_dir).resolve()
		self.interval = interval
		self.max_per_minute = max_per_minute
		self.wait_timeout = wait_timeout
		self._unpersisted_risk_lock: RiskLock | None = None

	@classmethod
	def for_data_dir(cls, data_dir: Path, *, interval: float = 1.5) -> RequestBudget:
		from boss_agent_cli.config import load_config

		root = budget_directory(data_dir)
		# An explicit group uses the group's configuration, not per-member limits.
		config = load_config(root / "config.json")
		from boss_agent_cli.config import validate_request_config

		validate_request_config(config)
		group_interval = max(interval, float(config["request_delay"][0])) if root != data_dir.resolve() else interval
		return cls(
			data_dir,
			root=root,
			interval=group_interval,
			max_per_minute=config["request_max_per_minute"],
			wait_timeout=config["request_wait_timeout"],
		)

	def check_risk(self) -> None:
		from boss_agent_cli.api import cdp_risk_lock
		from boss_agent_cli.api.client import PlatformRiskError

		if self._unpersisted_risk_lock is not None:
			raise PlatformRiskError.from_lock(self._unpersisted_risk_lock)
		cdp_risk_lock.raise_if_locked(self.data_dir)
		if self.root != self.data_dir:
			cdp_risk_lock.raise_if_locked(self.root)

	def record_risk(self, *, reason: str, platform_code: int | None) -> None:
		from boss_agent_cli.api import cdp_risk_lock

		self._unpersisted_risk_lock = cdp_risk_lock.RiskLock(
			stoken_sha256=None, locked_at=time.time(), reason=reason, platform_code=platform_code
		)
		failure: OSError | UnicodeError | None = None
		persisted = 0
		for directory in dict.fromkeys((self.data_dir, self.root)):
			try:
				cdp_risk_lock.write_lock(directory, None, reason=reason, platform_code=platform_code)
			except (OSError, UnicodeError) as error:
				failure = error
			else:
				# write_lock 自身已按「主位置 / 后备位置」汇总：一次调用成功就
				# 证明该目录至少有一处落盘。旧实现把「任一处失败」当成「全部
				# 失败」，信封里的 persisted=false 与磁盘事实相反（Issue #1921）。
				persisted += 1
		if persisted == 0:
			assert failure is not None
			raise failure
		self._unpersisted_risk_lock = None

	def _check(self, check: Callable[[], None] | None) -> None:
		self.check_risk()
		check_request_control(check)

	def _remaining(self, started: float) -> float:
		remaining = self.wait_timeout - (time.monotonic() - started)
		deadline = _CONTROL.get().deadline
		if deadline is not None:
			remaining = min(remaining, deadline - time.monotonic())
		return max(0.0, remaining)

	def _pause(
		self, seconds: float, started: float, check: Callable[[], None] | None, *, lease_poll: bool = False
	) -> None:
		self._check(check)
		if seconds > self._remaining(started) or self._remaining(started) <= 0:
			deadline = _CONTROL.get().deadline
			code = (
				"WORKFLOW_TIMEOUT"
				if deadline is not None and deadline <= started + self.wait_timeout
				else "REQUEST_BUDGET_EXHAUSTED"
			)
			# 租约轮询的 0.05s 切片不是真实等待值：窗口耗尽时对方还要持锁多久
			# 未知，报最后一个切片会让恢复指引按 50ms 立刻重试（Issue #1990）。
			raise RequestBudgetExceeded(
				"剩余时间内拿不到请求预算或等不完冷却，请求未发出",
				code=code,
				retry_after=None if lease_poll else seconds,
			)
		cancellable_sleep(seconds, check=lambda: self._check(check))

	def _check_wait_timeout(self, started: float) -> None:
		if time.monotonic() - started >= self.wait_timeout:
			raise RequestBudgetExceeded("请求节流等待超时，请求未发出")

	@staticmethod
	def _try_lease(handle: BinaryIO) -> bool:
		return try_lock_exclusive(handle)

	@staticmethod
	def _lease_names_locked_file(path: Path, handle: BinaryIO) -> bool:
		"""``path`` 是否仍指向 ``handle`` 打开、并已加锁的那个文件。

		租约文件在请求在途时被外部清理后，另一个进程会创建新 inode 并同样
		加锁成功——两个「唯一活跃操作」同时进入临界区（Issue #1919）。与
		services/file_lock._names_locked_file 同口径：POSIX 用 samestat 复核，
		Windows 不能删除仍被打开的文件，直接视为同一文件。
		"""
		if os.name == "nt":
			return True
		try:
			return os.path.samestat(os.stat(path), os.fstat(handle.fileno()))
		except OSError:
			return False

	@staticmethod
	def _try_directory_lease(root: Path) -> tuple[bool, int | None]:
		"""POSIX：锁住预算目录 inode；Windows 不能删除仍打开的文件，跳过。

		租约文件被外部清理后，新 inode 的锁与旧 inode 互不排斥，两个进程会同时
		进入临界区（Issue #1919）。目录本身在使用期间不会被删除，锁住它即可让
		所有实例继续互斥。返回 ``(是否支持目录租约, 持锁 fd)``：不支持时退回文件
		租约；忙时第二项为 None。
		"""
		if os.name == "nt":
			return False, None
		import errno
		import fcntl

		fd = os.open(root, os.O_RDONLY)
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except (BlockingIOError, PermissionError):
			os.close(fd)
			return True, None
		except OSError as error:
			os.close(fd)
			if error.errno in {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
				return False, None
			raise
		return True, fd

	@contextmanager
	def _lease(self, started: float, check: Callable[[], None] | None) -> Iterator[None]:
		key = (os.getpid(), threading.get_ident(), str(self.root))
		if key in _LEASES.get():
			self._check(check)
			yield
			return
		self._check(check)
		self.root.mkdir(parents=True, exist_ok=True)
		# 先拿目录租约：租约文件被删除 / 替换时，目录 inode 的锁仍然互斥
		# （Issue #1919）。文件系统不支持目录 flock 时退回文件租约。
		dir_fd: int | None = None
		while True:
			supported, fd = self._try_directory_lease(self.root)
			if not supported:
				break
			if fd is not None:
				dir_fd = fd
				break
			self._pause(0.05, started, check, lease_poll=True)
		try:
			path = self.root / "request-budget.lease"
			# 租约文件与 SQLite 库一样属于本地调度元数据：符号链接会让锁落到数据目录
			# 之外的目标，悬空链接会在目标处建文件，FIFO 会在 open("a+b") 永久阻塞
			# （Issue #1753）。与 services/file_lock 的锁文件同口径，直接 lstat。
			# 加锁成功后还要复核 path 仍指向被锁住的 inode：文件在 open 与加锁之间
			# 被替换时锁会落在孤儿 inode 上（Issue #1919）。
			handle: BinaryIO | None = None
			for _attempt in range(8):
				try:
					mode = path.lstat().st_mode
				except FileNotFoundError:
					mode = None
				except (OSError, ValueError) as exc:
					raise sqlite3.OperationalError(f"无法读取请求租约文件 {path} 的路径状态（{exc}）") from exc
				if mode is not None and stat.S_ISLNK(mode):
					raise sqlite3.OperationalError(f"{path} 是符号链接，请求租约拒绝写穿链接目标")
				if mode is not None and not stat.S_ISREG(mode):
					raise sqlite3.OperationalError(f"{path} 不是普通文件（FIFO / 目录 / 设备），请求租约拒绝打开")
				candidate = path.open("a+b")
				acquired = False
				try:
					# 写入与尺寸判断都按文件描述操作，避免 open 之后 path 被替换时把
					# 新 inode 的尺寸当成旧 inode 的（Issue #1919）。
					if os.fstat(candidate.fileno()).st_size == 0:
						candidate.write(b"\0")
						candidate.flush()
					while not self._try_lease(candidate):
						self._pause(0.05, started, check, lease_poll=True)
					acquired = self._lease_names_locked_file(path, candidate)
				finally:
					if not acquired:
						candidate.close()
				if acquired:
					handle = candidate
					break
			else:
				raise sqlite3.OperationalError(f"请求租约文件 {path} 在获取期间被反复替换")
			assert handle is not None
			token = _LEASES.set((*_LEASES.get(), key))
			try:
				self._check(check)
				yield
			except BaseException as error:
				from boss_agent_cli.api.client import PlatformRiskError

				if isinstance(error, PlatformRiskError) and "risk_lock" not in error.details:
					# 错误没有 risk_lock 详情时锁尚未落盘，由租约异常路径兜底记录。
					# 带 risk_lock 的错误来自已存在的锁（from_lock）或已在
					# _record_cdp_risk_lock 中落盘：重写会用缺失的顶层平台码覆盖
					# 原有诊断字段并刷新 locked_at（Issue #1989）。
					try:
						self.record_risk(
							reason="ACCOUNT_RISK" if error.code == "ACCOUNT_RISK" else "ENVIRONMENT_RISK",
							platform_code=error.details.get("platform_code"),
						)
					except OSError:
						pass  # Preserve the original risk if persistence fails.
				raise
			finally:
				_LEASES.reset(token)
				# Close releases the POSIX/Windows lease even after an exception.
				handle.close()
		finally:
			if dir_fd is not None:
				os.close(dir_fd)

	def _connect(self) -> sqlite3.Connection:
		path = self.root / "request-budget.sqlite3"
		# 符号链接会让 SQLite 把库写进目标文件（可能落在数据目录之外），目录 /
		# FIFO / 设备只会得到 "unable to open"；与 CacheStore / clean 同口径
		# 先按 lstat 判定（Issue #1739）。
		from boss_agent_cli.cache.store import database_path_problem

		problem = database_path_problem(path)
		if problem is not None:
			raise sqlite3.DatabaseError(
				f"{path} 不是可用的 SQLite 数据库（{problem}）；"
				"该文件只保存请求节流元数据，确认后移除该路径即可自动重建"
			)
		try:
			connection = sqlite3.connect(path, timeout=0.1)
			connection.executescript("""
				CREATE TABLE IF NOT EXISTS request_state (
					id INTEGER PRIMARY KEY CHECK(id=1), last_at REAL NOT NULL DEFAULT 0,
					next_at REAL NOT NULL DEFAULT 0, cooldown_until REAL NOT NULL DEFAULT 0
				);
				INSERT OR IGNORE INTO request_state(id) VALUES(1);
				CREATE TABLE IF NOT EXISTS request_attempts (
					id INTEGER PRIMARY KEY, requested_at REAL NOT NULL, kind TEXT NOT NULL
				);
			""")
		except sqlite3.DatabaseError as exc:
			# 锁冲突保持原有语义（等锁释放即可）；本地文件 / 磁盘错误——只读库、
			# 只读目录、磁盘满、设备 I/O 故障——与库内容无关，删库既修不好又会
			# 丢掉冷却时间与节流历史，必须保留原始 OperationalError，让命令层
			# 报 LOCAL_IO_ERROR 的通用指引（Issue #1721，与 #1467/#1718 同口径）。
			# 其余才是文件损坏 / 被同名目录占位：带上路径与自愈方式，命令层统一
			# 报 LOCAL_IO_ERROR，而不是 NETWORK_ERROR +「重试」让 Agent 反复打
			# 平台（Issue #1336）。
			from boss_agent_cli.cache.store import is_cache_busy_error, is_cache_local_io_error

			if is_cache_busy_error(exc) or is_cache_local_io_error(exc):
				raise
			raise sqlite3.DatabaseError(
				f"{path} 不是可用的 SQLite 数据库（{exc}）；"
				"该文件只保存请求节流元数据，备份后删除即可自动重建"
			) from exc
		return connection

	@contextmanager
	def _database(self) -> Iterator[sqlite3.Connection]:
		connection = self._connect()
		try:
			with connection:
				yield connection
		finally:
			connection.close()

	@contextmanager
	def attempt(
		self,
		kind: str = "api",
		*,
		check: Callable[[], None] | None = None,
		before_wait: Callable[[], None] | None = None,
		claim: Callable[[], None] | None = None,
	) -> Iterator[None]:
		started = time.monotonic()
		with self._lease(started, check):
			if before_wait is not None:
				with request_control(check=lambda: self._check_wait_timeout(started)):
					before_wait()
			self._check(check)
			while True:
				self._check(check)
				now = time.time()
				with self._database() as db:
					# typeof 漂移的行既不会被数值比较删除，也会让 MIN() 返回文本；
					# 本地调度元数据无保留价值，直接归零让下一轮自愈。
					db.execute(
						"UPDATE request_state SET last_at=0,next_at=0 "
						"WHERE typeof(last_at) NOT IN ('integer','real') OR typeof(next_at) NOT IN ('integer','real')"
					)
					db.execute(
						"UPDATE request_state SET cooldown_until=0 WHERE typeof(cooldown_until) NOT IN ('integer','real')"
					)
					last_at, next_at, cooldown = db.execute(
						"SELECT last_at,next_at,cooldown_until FROM request_state WHERE id=1"
					).fetchone()
					last_at = _tolerated_timestamp(last_at)
					next_at = _tolerated_timestamp(next_at)
					cooldown = _tolerated_timestamp(cooldown)
					# 有限的远未来值与 inf/nan 一样是漂移（Issue #1422）：
					# next_at 的合法上限是 now + interval，超过后拒绝等待的
					# 路径不会写回，所有后续命令都会卡在同一条坏记录上。
					future_limit = now + self.interval + _FUTURE_SKEW_SECONDS
					reset_last_at = last_at > future_limit
					reset_next_at = next_at > future_limit
					if reset_last_at:
						last_at = 0.0
					if reset_next_at:
						next_at = 0.0
					# cooldown_until 来自服务器 Retry-After，单独用更宽的上限；
					# 合法的短冷却保持原值，超过上限则截断并写回自愈。
					clamped_cooldown = min(cooldown, now + _MAX_COOLDOWN_SECONDS)
					if reset_last_at or reset_next_at or clamped_cooldown != cooldown:
						cooldown = clamped_cooldown
						db.execute(
							"UPDATE request_state SET last_at=?,next_at=?,cooldown_until=? WHERE id=1",
							(last_at, next_at, cooldown),
						)
					db.execute("DELETE FROM request_attempts WHERE requested_at <= ?", (now - 60.0,))
					# 未来的尝试记录同样是漂移：它永不满足清理条件，会一直占住
					# 每分钟名额并把 window_ready 推到不可达的时间点。
					db.execute(
						"DELETE FROM request_attempts WHERE requested_at > ?",
						(now + _FUTURE_SKEW_SECONDS,),
					)
					db.execute("DELETE FROM request_attempts WHERE typeof(requested_at) NOT IN ('integer','real')")
					# 自愈写回先落盘：下面的超时拒绝（raise 会回滚本事务）或
					# claim 失败都不能让漂移记录原样留在库里。
					db.commit()
					count, oldest = db.execute("SELECT COUNT(*),MIN(requested_at) FROM request_attempts").fetchone()
					window_ready = _tolerated_timestamp(oldest) + 60.0 if count >= self.max_per_minute else 0.0
					ready = max(next_at, last_at + self.interval if last_at else 0.0, cooldown, window_ready)
					wait = max(0.0, ready - now)
					if wait <= 0:
						self._check(check)
						if self._remaining(started) <= 0:
							raise RequestBudgetExceeded("请求节流等待超时，请求未发出")
						if claim is not None:
							claim()
						db.execute("INSERT INTO request_attempts(requested_at,kind) VALUES(?,?)", (now, kind))
						db.execute(
							"UPDATE request_state SET last_at=?,next_at=? WHERE id=1", (now, now + self.interval)
						)
						db.commit()
						break
				self._pause(wait, started, check)
			self._check(check)
			yield

	def cooldown(self, seconds: float) -> None:
		"""Store server/explicit rate-limit feedback without waiting or unlocking."""
		if not math.isfinite(seconds) or seconds < 0:
			raise ValueError("cooldown must be finite and nonnegative")
		# Response feedback must survive a stop/deadline which arrived in flight.
		# An active operation already holds the lease; this write sends nothing.
		key = (os.getpid(), threading.get_ident(), str(self.root))
		if key in _LEASES.get():
			self._store_cooldown(seconds)
			return
		with self._lease(time.monotonic(), None):
			self._store_cooldown(seconds)

	def _store_cooldown(self, seconds: float) -> None:
		now = time.time()
		with self._database() as db:
			row = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()
			# 已有值漂移成文本 / inf 时 SQL 的 MAX(text, real) 会选中文本，
			# 新冷却被静默吞掉（服务器冷却失效）。先归一再取较大值。
			existing = _tolerated_timestamp(row[0]) if row is not None else 0.0
			# 有限的远未来值同样是漂移：不归零会让 MAX() 永远选中坏值，
			# 合法的服务器冷却再也写不进去（Issue #1422）。
			if existing > now + _MAX_COOLDOWN_SECONDS:
				existing = 0.0
			# 服务器 Retry-After 也有本地上限：一个坏值不能把预算推到多年以后。
			until = min(max(existing, now + seconds), now + _MAX_COOLDOWN_SECONDS)
			db.execute("UPDATE request_state SET cooldown_until=? WHERE id=1", (until,))
