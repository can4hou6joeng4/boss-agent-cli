"""Process-local pacing; shared dispatch is coordinated by RequestBudget.

Reservations and completions have separate roles. Completing an earlier request
must never move a later reservation backward. Delays are local limits, not a
guarantee that the platform will accept a request.
"""

import random
import threading
import time
from collections import deque
from collections.abc import Callable

from boss_agent_cli.config import DEFAULT_REQUEST_DELAY
from boss_agent_cli.api.request_budget import cancellable_sleep, validate_delay


class RequestThrottle:
	"""Rate limiter with Gaussian-distributed delays and burst detection."""

	def __init__(self, delay: tuple[float, float] = DEFAULT_REQUEST_DELAY, *, check: Callable[[], None] | None = None) -> None:
		validate_delay(delay)
		self._delay = delay
		self._check = check
		self._last_request_time = 0.0
		self._recent_times: deque[float] = deque(maxlen=12)
		self._lock = threading.Lock()

	def wait(self) -> None:
		"""Block until it's safe to send the next request.

		并发安全：在锁内基于"已预约的下一次发送时刻"计算等待并预占该时刻，
		使后到的线程读到的是未来时间而被迫顺延，从而真正串行化、杜绝突发；
		实际 sleep 在锁外进行，不阻塞其他线程的预约计算。
		"""
		with self._lock:
			now = time.monotonic()
			elapsed = now - self._last_request_time
			mean = sum(self._delay) / 2
			std = (self._delay[1] - self._delay[0]) / 4
			interval = min(self._delay[1], max(self._delay[0], random.gauss(mean, std)))
			base_sleep = max(0, interval - elapsed)

			# Additional pacing only; the configured lower bound always holds.
			if random.random() < 0.05:
				base_sleep += random.uniform(2.0, 5.0)

			burst = self._burst_penalty()
			total = max(0, base_sleep + burst)
			# 预占下一次发送时刻：并发线程会据此顺延，避免读到旧值而突发
			self._last_request_time = now + total

		cancellable_sleep(total, check=self._check)

	def mark(self) -> None:
		"""Record that a request was just sent."""
		with self._lock:
			now = time.monotonic()
			self._last_request_time = max(self._last_request_time, now)
			self._recent_times.append(now)

	def _burst_penalty(self) -> float:
		"""Extra delay when requests arrive in bursts. 调用方须已持锁。"""
		if not self._recent_times:
			return 0.0
		now = time.monotonic()
		recent_15s = sum(1 for ts in self._recent_times if now - ts <= 15)
		recent_45s = sum(1 for ts in self._recent_times if now - ts <= 45)
		# 45s 内 >=6 次请求：重罚
		if recent_45s >= 6:
			return random.uniform(4.0, 7.0)
		# 15s 内 >=3 次请求：轻罚
		if recent_15s >= 3:
			return random.uniform(1.2, 2.8)
		return 0.0
