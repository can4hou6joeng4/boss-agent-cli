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
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

if TYPE_CHECKING:
	from boss_agent_cli.api.cdp_risk_lock import RiskLock


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
	from boss_agent_cli.config import load_config

	value = load_config(data_dir / "config.json").get("request_budget_dir")
	if value is None:
		return data_dir.resolve()
	if not isinstance(value, str) or not value.strip():
		raise ValueError("request_budget_dir must be a nonempty absolute path or null")
	path = Path(value).expanduser()
	if not path.is_absolute():
		raise ValueError("request_budget_dir must be an absolute path")
	return path.resolve()


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
		failure: OSError | None = None
		for directory in dict.fromkeys((self.data_dir, self.root)):
			try:
				cdp_risk_lock.write_lock(directory, None, reason=reason, platform_code=platform_code)
			except OSError as error:
				failure = error
		if failure is not None:
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

	def _pause(self, seconds: float, started: float, check: Callable[[], None] | None) -> None:
		self._check(check)
		if seconds > self._remaining(started) or self._remaining(started) <= 0:
			deadline = _CONTROL.get().deadline
			code = (
				"WORKFLOW_TIMEOUT"
				if deadline is not None and deadline <= started + self.wait_timeout
				else "REQUEST_BUDGET_EXHAUSTED"
			)
			raise RequestBudgetExceeded(
				"剩余时间内拿不到请求预算或等不完冷却，请求未发出", code=code, retry_after=seconds
			)
		cancellable_sleep(seconds, check=lambda: self._check(check))

	def _check_wait_timeout(self, started: float) -> None:
		if time.monotonic() - started >= self.wait_timeout:
			raise RequestBudgetExceeded("请求节流等待超时，请求未发出")

	@staticmethod
	def _try_lease(handle: BinaryIO) -> bool:
		try:
			if os.name == "nt":
				import msvcrt

				handle.seek(0)
				windows_lock = cast(Any, msvcrt)
				windows_lock.locking(handle.fileno(), windows_lock.LK_NBLCK, 1)
			else:
				import fcntl

				fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
		except (BlockingIOError, PermissionError):
			return False
		except OSError as error:
			if error.errno in (11, 13, 35, 36):
				return False
			raise
		return True

	@contextmanager
	def _lease(self, started: float, check: Callable[[], None] | None) -> Iterator[None]:
		key = (os.getpid(), threading.get_ident(), str(self.root))
		if key in _LEASES.get():
			self._check(check)
			yield
			return
		self._check(check)
		self.root.mkdir(parents=True, exist_ok=True)
		path = self.root / "request-budget.lease"
		with path.open("a+b") as handle:
			if path.stat().st_size == 0:
				handle.write(b"\0")
				handle.flush()
			while not self._try_lease(handle):
				self._pause(0.05, started, check)
			token = _LEASES.set((*_LEASES.get(), key))
			try:
				self._check(check)
				yield
			except BaseException as error:
				from boss_agent_cli.api.client import PlatformRiskError

				if isinstance(error, PlatformRiskError):
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

	def _connect(self) -> sqlite3.Connection:
		connection = sqlite3.connect(self.root / "request-budget.sqlite3", timeout=0.1)
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
					last_at, next_at, cooldown = db.execute(
						"SELECT last_at,next_at,cooldown_until FROM request_state WHERE id=1"
					).fetchone()
					db.execute("DELETE FROM request_attempts WHERE requested_at <= ?", (now - 60.0,))
					count, oldest = db.execute("SELECT COUNT(*),MIN(requested_at) FROM request_attempts").fetchone()
					window_ready = oldest + 60.0 if count >= self.max_per_minute else 0.0
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
		with self._database() as db:
			db.execute(
				"UPDATE request_state SET cooldown_until=MAX(cooldown_until,?) WHERE id=1", (time.time() + seconds,)
			)
