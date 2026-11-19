import time
from unittest.mock import patch

from boss_agent_cli.api.throttle import RequestThrottle


def test_throttle_mark_records_time():
	t = RequestThrottle(delay=(0.0, 0.0))
	# 首次哨兵不再是 0.0：单调时钟原点未定义，初始化即视为刚过一个最大间隔
	# （Issue #1871）。
	assert t._last_request_time <= time.monotonic()
	t.mark()
	assert t._last_request_time > 0
	assert len(t._recent_times) == 1


def test_throttle_wait_no_delay():
	"""delay=(0,0) 时 wait 几乎不阻塞"""
	t = RequestThrottle(delay=(0.0, 0.0))
	t.mark()
	start = time.time()
	with patch("boss_agent_cli.api.throttle.random") as mock_rand:
		mock_rand.gauss.return_value = 0.0
		mock_rand.random.return_value = 0.5  # 不触发随机长停顿
		mock_rand.uniform.return_value = 0.0
		t.wait()
	elapsed = time.time() - start
	assert elapsed < 0.5


def test_throttle_burst_penalty_light():
	"""15s 内 3+ 次请求触发轻罚"""
	t = RequestThrottle(delay=(0.0, 0.0))
	now = time.monotonic()
	for _ in range(3):
		t._recent_times.append(now)
	penalty = t._burst_penalty()
	assert 1.2 <= penalty <= 2.8


def test_throttle_burst_penalty_heavy():
	"""45s 内 6+ 次请求触发重罚"""
	t = RequestThrottle(delay=(0.0, 0.0))
	now = time.monotonic()
	for _ in range(6):
		t._recent_times.append(now)
	penalty = t._burst_penalty()
	assert 4.0 <= penalty <= 7.0


def test_throttle_no_burst_penalty():
	"""请求少时无惩罚"""
	t = RequestThrottle(delay=(0.0, 0.0))
	assert t._burst_penalty() == 0.0
	t._recent_times.append(time.monotonic())
	assert t._burst_penalty() == 0.0
