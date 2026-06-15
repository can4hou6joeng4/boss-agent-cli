"""Shared request throttling — Gaussian delay + burst penalty.

Used by both httpx and browser channels to avoid triggering BOSS anti-scraping.
线程安全：welfare 详情风暴会用线程池并发调用，wait()/mark() 共享可变状态
（_last_request_time / _recent_times），故全程加锁，避免竞态导致的请求突发
（突发更像爬虫，是合规风险）。
"""

import random
import threading
import time
from collections import deque


class RequestThrottle:
	"""Rate limiter with Gaussian-distributed delays and burst detection."""

	def __init__(self, delay: tuple[float, float] = (1.5, 3.0)) -> None:
		self._delay = delay
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
			now = time.time()
			elapsed = now - self._last_request_time
			mean = sum(self._delay) / 2
			std = (self._delay[1] - self._delay[0]) / 4
			base_sleep = max(0, random.gauss(mean, std) - elapsed)

			# 5% chance of a longer pause — mimics human hesitation
			if random.random() < 0.05:
				base_sleep += random.uniform(2.0, 5.0)

			burst = self._burst_penalty()
			total = max(0, base_sleep + burst)
			# 预占下一次发送时刻：并发线程会据此顺延，避免读到旧值而突发
			self._last_request_time = now + total

		if total > 0:
			time.sleep(total)

	def mark(self) -> None:
		"""Record that a request was just sent."""
		with self._lock:
			now = time.time()
			self._last_request_time = now
			self._recent_times.append(now)

	def _burst_penalty(self) -> float:
		"""Extra delay when requests arrive in bursts. 调用方须已持锁。"""
		if not self._recent_times:
			return 0.0
		now = time.time()
		recent_15s = sum(1 for ts in self._recent_times if now - ts <= 15)
		recent_45s = sum(1 for ts in self._recent_times if now - ts <= 45)
		# 45s 内 >=6 次请求：重罚
		if recent_45s >= 6:
			return random.uniform(4.0, 7.0)
		# 15s 内 >=3 次请求：轻罚
		if recent_15s >= 3:
			return random.uniform(1.2, 2.8)
		return 0.0
