"""批次 115：等待上限必须容得下刻意节流（Issue #2461）。"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from boss_agent_cli.api import request_budget as request_budget_module
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.request_budget import RequestBudget, RequestBudgetExceeded
from boss_agent_cli.api.throttle import RequestThrottle
from boss_agent_cli.config import DEFAULTS

_BASE_MONOTONIC = 1000.0
_BASE_EPOCH = 1_760_000_000.0


def _write_config(data_dir: Path, value: dict[str, object]) -> None:
	(data_dir / "config.json").write_text(json.dumps(value), encoding="utf-8")


# ── 默认值与 for_data_dir 的节流下限 ────────────────────────────────


def test_default_wait_timeout_covers_pacing_high() -> None:
	assert DEFAULTS["request_wait_timeout"] >= DEFAULTS["request_delay"][1] + 15


def test_for_data_dir_defaults_keep_120(tmp_path: Path) -> None:
	budget = RequestBudget.for_data_dir(tmp_path, interval=30.0, pacing_limit=60.0)

	assert budget.wait_timeout == 120.0
	assert budget.interval == 30.0


def test_for_data_dir_floors_explicit_small_timeout(tmp_path: Path) -> None:
	_write_config(tmp_path, {"request_wait_timeout": 30})

	budget = RequestBudget.for_data_dir(tmp_path, interval=30.0, pacing_limit=60.0)

	assert budget.wait_timeout == 75.0


def test_for_data_dir_without_pacing_limit_keeps_explicit_value(tmp_path: Path) -> None:
	_write_config(tmp_path, {"request_wait_timeout": 12})

	budget = RequestBudget.for_data_dir(tmp_path, interval=1.5)

	assert budget.wait_timeout == 12.0


def test_group_delay_high_is_part_of_the_floor(tmp_path: Path) -> None:
	member = tmp_path / "member"
	group = tmp_path / "group"
	member.mkdir()
	group.mkdir()
	_write_config(member, {"request_budget_dir": str(group)})
	_write_config(group, {"request_delay": [90, 120]})

	budget = RequestBudget.for_data_dir(member, interval=30.0, pacing_limit=60.0)

	assert budget.wait_timeout == 135.0


# ── 客户端接线：节流上限必须传进预算 ────────────────────────────────


class _FakeAuthManager:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "stoken", "user_agent": "ua"}

	def get_token(self) -> dict[str, object]:
		return self.token


def test_client_passes_pacing_limit_to_budget(tmp_path: Path) -> None:
	_write_config(tmp_path, {"request_wait_timeout": 30})

	client = BossClient(_FakeAuthManager(tmp_path))

	assert client._request_budget is not None
	assert client._request_budget.wait_timeout == 75.0


# ── 假时钟：默认节奏下连续两次请求都能成功 ──────────────────────────


class _Clock:
	def __init__(self) -> None:
		self.monotonic = _BASE_MONOTONIC
		self.epoch = _BASE_EPOCH

	def advance(self, seconds: float) -> None:
		self.monotonic += seconds
		self.epoch += seconds


def test_second_request_succeeds_under_default_pacing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	clock = _Clock()
	# request_budget 与 throttle 引用同一个 time 模块对象：一次替换即可驱动两侧。
	monkeypatch.setattr(request_budget_module.time, "monotonic", lambda: clock.monotonic)
	monkeypatch.setattr(request_budget_module.time, "time", lambda: clock.epoch)
	monkeypatch.setattr(request_budget_module.time, "sleep", clock.advance)
	random.seed(3)

	budget = RequestBudget(tmp_path, interval=30.0, wait_timeout=75.0)
	throttle = RequestThrottle((30.0, 60.0))

	with budget.attempt("api", before_wait=throttle.wait):
		throttle.mark()
	with budget.attempt("api", before_wait=throttle.wait):
		throttle.mark()

	# 第二次确实等了一个真实间隔，但落在 75 秒上限内。
	assert clock.monotonic - _BASE_MONOTONIC >= 30.0


def test_pacing_beyond_timeout_still_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	clock = _Clock()
	monkeypatch.setattr(request_budget_module.time, "monotonic", lambda: clock.monotonic)
	monkeypatch.setattr(request_budget_module.time, "time", lambda: clock.epoch)
	monkeypatch.setattr(request_budget_module.time, "sleep", clock.advance)
	random.seed(3)

	# 显式把上限压到远低于节流间隔：不可发出时必须 fail-closed，而不是提前发送。
	first = RequestBudget(tmp_path, interval=0.0, wait_timeout=30.0)
	throttle = RequestThrottle((30.0, 60.0))
	with first.attempt("api", before_wait=throttle.wait):
		throttle.mark()

	strict = RequestBudget(tmp_path, interval=0.0, wait_timeout=5.0)
	with pytest.raises(RequestBudgetExceeded) as exc_info:
		with strict.attempt("api", before_wait=throttle.wait):
			throttle.mark()

	assert exc_info.value.details["request_sent"] is False
	assert clock.monotonic - _BASE_MONOTONIC >= 5.0
