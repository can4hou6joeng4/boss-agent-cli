"""Tests for boss stats 命令。"""

import json
import sqlite3
import time
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path: Path, *args):
	runner = CliRunner()
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "stats", *args])


def _seed_cache(tmp_path: Path, greet: int = 0, applied: int = 0, shortlist: int = 0):
	"""初始化缓存表并插入测试数据。"""
	from boss_agent_cli.cache.store import CacheStore

	CacheStore(tmp_path / "cache" / "boss_agent.db").close()
	conn = sqlite3.connect(str(tmp_path / "cache" / "boss_agent.db"))
	now = time.time()
	for i in range(greet):
		conn.execute(
			"INSERT OR REPLACE INTO greet_records VALUES (?, ?, ?)",
			(f"sid-{i}", f"jid-{i}", now),
		)
	for i in range(applied):
		conn.execute(
			"INSERT OR REPLACE INTO apply_records VALUES (?, ?, ?)",
			(f"sid-{i}", f"jid-{i}", now),
		)
	for i in range(shortlist):
		conn.execute(
			"INSERT OR REPLACE INTO shortlist_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			(f"sid-{i}", f"jid-{i}", "职位", "公司", "北京", "20-30K", "search", now),
		)
	conn.commit()
	conn.close()


def test_stats_empty_cache(tmp_path):
	"""未建立缓存时返回零值和提示。"""
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["funnel"]["greeted"] == 0
	assert "缓存尚未建立" in parsed["data"]["note"]


def test_stats_funnel_counts(tmp_path):
	"""漏斗基数与转化率计算正确。"""
	_seed_cache(tmp_path, greet=10, applied=3, shortlist=2)
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	funnel = parsed["data"]["funnel"]
	assert funnel["greeted"] == 10
	assert funnel["applied"] == 3
	assert funnel["shortlist"] == 2
	conv = parsed["data"]["conversion"]
	assert conv["apply_rate"] == 0.3
	assert conv["shortlist_rate"] == 0.2


def test_stats_window_days_option(tmp_path):
	"""--days 参数被透传到窗口统计。"""
	_seed_cache(tmp_path, greet=1)
	result = _invoke(tmp_path, "--days", "7")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["window_days"] == 7
	assert parsed["data"]["window"]["greeted"] == 1


def test_stats_zero_greet_no_divide_by_zero(tmp_path):
	"""无打招呼记录时转化率为 0，不抛异常。"""
	_seed_cache(tmp_path, greet=0, applied=0, shortlist=0)
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["conversion"]["apply_rate"] == 0.0


def test_stats_registered_in_main_and_schema(tmp_path):
	"""stats 命令已注册到 main.py 和 schema。"""
	runner = CliRunner()
	schema_result = runner.invoke(cli, ["schema"])
	assert schema_result.exit_code == 0
	parsed = json.loads(schema_result.output)
	assert "stats" in parsed["data"]["commands"]
	assert parsed["data"]["commands"]["stats"]["description"]
