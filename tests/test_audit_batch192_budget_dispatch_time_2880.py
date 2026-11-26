"""预算按本地准备完成的时间登记，不能压缩后续派发间隔（Issue #2880）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from boss_agent_cli.api import request_budget as module


class _Clock:
	def __init__(self) -> None:
		self.wall = 2_000_000_000.0
		self.monotonic = 100.0

	def advance(self, seconds: float) -> None:
		self.wall += seconds
		self.monotonic += seconds


@pytest.mark.parametrize("slow_stage", ["database", "claim"])
def test_local_preparation_does_not_shorten_next_dispatch_interval(tmp_path: Path, monkeypatch, slow_stage: str) -> None:
	"""数据库或预约准备耗时大于间隔后，下一次操作仍须等待完整间隔。"""
	clock = _Clock()
	monkeypatch.setattr(module.time, "time", lambda: clock.wall)
	monkeypatch.setattr(module.time, "monotonic", lambda: clock.monotonic)
	monkeypatch.setattr(module.time, "sleep", clock.advance)
	original_database = module.RequestBudget._database
	prepared = False

	@contextmanager
	def database(budget: module.RequestBudget) -> Iterator[sqlite3.Connection]:
		nonlocal prepared
		with original_database(budget) as connection:
			if slow_stage == "database" and not prepared:
				clock.advance(0.2)
				prepared = True
			yield connection

	monkeypatch.setattr(module.RequestBudget, "_database", database)

	def claim() -> None:
		if slow_stage == "claim":
			clock.advance(0.2)

	dispatches = []
	for index, kind in enumerate(("api", "browser-fetch")):
		budget = module.RequestBudget(tmp_path, interval=0.12, wait_timeout=3)
		with budget.attempt(kind, claim=claim if index == 0 else None):
			dispatches.append(clock.wall)

	assert dispatches[1] - dispatches[0] == pytest.approx(0.12, abs=1e-6)
	# 分钟窗口也要使用派发前的新时间，而不是准备工作开始时的旧时间。
	with sqlite3.connect(tmp_path / "request-budget.sqlite3") as connection:
		attempts = connection.execute("SELECT requested_at FROM request_attempts ORDER BY id").fetchall()
	assert [row[0] for row in attempts] == pytest.approx(dispatches, rel=0, abs=1e-6)


def test_failed_claim_does_not_register_a_dispatch(tmp_path: Path) -> None:
	"""派发前预约失败时，重新采样时间不能提前登记一次根本没有派发的请求。"""
	budget = module.RequestBudget(tmp_path, interval=0.12, wait_timeout=3)

	def fail_claim() -> None:
		raise ValueError("预约失败")

	with pytest.raises(ValueError, match="预约失败"):
		with budget.attempt("api", claim=fail_claim):
			pytest.fail("预约失败后不能派发")

	with sqlite3.connect(tmp_path / "request-budget.sqlite3") as connection:
		assert connection.execute("SELECT COUNT(*) FROM request_attempts").fetchone()[0] == 0
