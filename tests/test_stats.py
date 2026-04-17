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


# ── HTML 格式扩展 ─────────────────────────────────────────────


def test_stats_html_format_to_stdout(tmp_path):
	"""--format html 无 -o 时 HTML 直出 stdout。"""
	_seed_cache(tmp_path, greet=3, applied=1, shortlist=1)
	runner = CliRunner()
	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path), "stats", "--format", "html",
	])
	assert result.exit_code == 0
	assert "<!doctype html>" in result.output
	assert "<title>boss-agent-cli 投递漏斗报表</title>" in result.output
	# 数据点应嵌入到 HTML
	assert ">3<" in result.output  # greeted 显示
	assert "33.33%" in result.output or "0.3333%" in result.output or "33.33" in result.output  # apply_rate


def test_stats_html_format_to_file(tmp_path):
	"""--format html -o 写文件时 stdout 返回 JSON 信封。"""
	_seed_cache(tmp_path, greet=10, applied=5, shortlist=3)
	out_path = tmp_path / "report.html"
	result = _invoke(tmp_path, "--format", "html", "-o", str(out_path))
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "html"
	assert parsed["data"]["path"] == str(out_path)
	assert parsed["data"]["bytes"] > 1000
	# 文件真的写了
	assert out_path.exists()
	html_content = out_path.read_text(encoding="utf-8")
	assert "<!doctype html>" in html_content
	assert "10" in html_content  # greeted
	assert "50.0%" in html_content  # apply_rate


def test_stats_html_empty_cache(tmp_path):
	"""无缓存时 HTML 也能生成，显示 note。"""
	result = _invoke(tmp_path, "--format", "html", "-o", str(tmp_path / "r.html"))
	assert result.exit_code == 0
	html_content = (tmp_path / "r.html").read_text(encoding="utf-8")
	assert "缓存尚未建立" in html_content


def test_stats_html_self_contained_no_external_deps(tmp_path):
	"""HTML 报表不应引入外部 CDN 或 script src。"""
	_seed_cache(tmp_path, greet=5, applied=2)
	result = _invoke(tmp_path, "--format", "html", "-o", str(tmp_path / "r.html"))
	assert result.exit_code == 0
	html_content = (tmp_path / "r.html").read_text(encoding="utf-8")
	# 安全约束：不得有外部 script / link 引用
	assert '<script src=' not in html_content
	assert '<link rel="stylesheet" href=' not in html_content
	assert 'cdn.' not in html_content
	assert 'googleapis' not in html_content


def test_stats_html_format_invalid_raises(tmp_path):
	"""非法 --format 值应被 Click 拦截。"""
	result = _invoke(tmp_path, "--format", "xml")
	assert result.exit_code != 0
