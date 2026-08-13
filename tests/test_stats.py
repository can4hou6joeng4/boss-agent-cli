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


def _seed_cache(
	tmp_path: Path,
	greet: int = 0,
	applied: int = 0,
	shortlist: int = 0,
	watch_hits: int = 0,
):
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
			"INSERT OR REPLACE INTO shortlist_records "
			"(security_id, job_id, title, company, city, salary, source, created_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			(f"sid-{i}", f"jid-{i}", "职位", "公司", "北京", "20-30K", "search", now),
		)
	for i in range(watch_hits):
		conn.execute(
			"INSERT OR REPLACE INTO watch_hits VALUES (?, ?, ?, ?, ?)",
			("daily", f"sid-{i}:jid-{i}", "{}", now, now),
		)
	conn.commit()
	conn.close()


def test_stats_empty_cache(tmp_path):
	"""未建立缓存时返回零值和提示。"""
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["funnel"] == {
		"greeted": 0,
		"applied": 0,
		"shortlist": 0,
		"watch_hits": 0,
	}
	assert parsed["data"]["conversion"] == {"apply_rate": 0.0, "shortlist_rate": 0.0}
	assert parsed["data"]["window_days"] == 30
	assert "缓存尚未建立" in parsed["data"]["note"]
	assert parsed["hints"]["next_actions"] == [
		"boss search <query> 搜索职位",
		"boss pipeline 查看候选进度",
		"boss follow-up 查看需要跟进的联系人",
	]


def test_stats_funnel_counts(tmp_path):
	"""漏斗基数与转化率计算正确。"""
	_seed_cache(tmp_path, greet=10, applied=3, shortlist=2, watch_hits=4)
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	funnel = parsed["data"]["funnel"]
	assert funnel["greeted"] == 10
	assert funnel["applied"] == 3
	assert funnel["shortlist"] == 2
	assert parsed["data"]["window"] == {
		"greeted": 10,
		"applied": 3,
		"shortlist": 2,
		"watch_hits": 4,
	}
	conv = parsed["data"]["conversion"]
	assert conv["apply_rate"] == 0.3
	assert conv["shortlist_rate"] == 0.2
	assert conv["apply_rate_window"] == 0.3
	assert parsed["hints"]["next_actions"] == [
		"boss pipeline 查看候选进度",
		"boss follow-up 查看需要跟进的联系人",
	]


def test_stats_window_days_option(tmp_path):
	"""--days 参数被透传到窗口统计。"""
	_seed_cache(tmp_path, greet=1)
	result = _invoke(tmp_path, "--days", "7")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["window_days"] == 7
	assert parsed["data"]["window"] == {
		"greeted": 1,
		"applied": 0,
		"shortlist": 0,
		"watch_hits": 0,
	}


def test_stats_zero_greet_no_divide_by_zero(tmp_path):
	"""无打招呼记录时转化率为 0，不抛异常。"""
	_seed_cache(tmp_path, greet=0, applied=0, shortlist=0)
	result = _invoke(tmp_path)
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["conversion"] == {
		"apply_rate": 0.0,
		"shortlist_rate": 0.0,
		"apply_rate_window": 0.0,
	}


def test_stats_registered_in_main_and_schema(tmp_path):
	"""stats 命令已注册到 main.py 和 schema。"""
	runner = CliRunner()
	schema_result = runner.invoke(cli, ["schema"])
	assert schema_result.exit_code == 0
	parsed = json.loads(schema_result.output)
	stats_schema = parsed["data"]["commands"]["stats"]
	assert stats_schema["description"]
	assert stats_schema["options"]["--days"]["default"] == 30
	assert stats_schema["options"]["--format"]["default"] == "json"
	assert stats_schema["availability"] == {
		"roles": ["candidate"],
		"candidate_platforms": ["zhilian", "zhipin"],
		"recruiter_platforms": [],
	}


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


# ── TTY 渲染（真人链路）────────────────────────────────────────────


def _capture_console(monkeypatch):
	"""替换 display.console 为可捕获的非终端 Console，返回缓冲区。"""
	import io

	from rich.console import Console

	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=100))
	return stream


def test_render_stats_shows_funnel_counts(monkeypatch):
	"""渲染应展示漏斗各阶段计数，而不是 JSON。"""
	from boss_agent_cli.commands.stats import _render_stats

	stream = _capture_console(monkeypatch)
	_render_stats({
		"window_days": 30,
		"funnel": {"greeted": 12, "applied": 3, "shortlist": 8},
		"window": {"greeted": 5, "applied": 1, "shortlist": 2, "watch_hits": 4},
		"conversion": {"apply_rate": 0.25, "shortlist_rate": 0.6667, "apply_rate_window": 0.2},
	})
	out = stream.getvalue()

	assert "12" in out and "3" in out and "8" in out
	assert "打招呼" in out
	assert "{" not in out, "TTY 渲染不应出现 JSON"


def test_render_stats_shows_conversion_rate_as_percent(monkeypatch):
	"""转化率应渲染为百分比，而不是裸小数。"""
	from boss_agent_cli.commands.stats import _render_stats

	stream = _capture_console(monkeypatch)
	_render_stats({
		"window_days": 30,
		"funnel": {"greeted": 12, "applied": 3, "shortlist": 8},
		"window": {"greeted": 5, "applied": 1, "shortlist": 2, "watch_hits": 4},
		"conversion": {"apply_rate": 0.25, "shortlist_rate": 0.6667, "apply_rate_window": 0.2},
	})
	out = stream.getvalue()

	assert "25.0%" in out
	assert "0.25" not in out


def test_render_stats_empty_state_gives_next_step(monkeypatch):
	"""空数据态必须给出可执行的下一步，而不是空表骨架（AC4）。"""
	from boss_agent_cli.commands.stats import _render_stats

	stream = _capture_console(monkeypatch)
	_render_stats({
		"funnel": {"greeted": 0, "applied": 0, "shortlist": 0, "watch_hits": 0},
		"conversion": {"apply_rate": 0.0, "shortlist_rate": 0.0},
		"window_days": 30,
		"note": "缓存尚未建立，先跑一次 search/greet/apply 再查看",
	})
	out = stream.getvalue()

	assert "boss search" in out, "空态应提示下一步命令"
	assert "{" not in out


def test_stats_tty_path_writes_nothing_to_stdout(tmp_path, monkeypatch):
	"""TTY 下 render 生效时 stdout 必须干净（AC2）。"""
	_capture_console(monkeypatch)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "stats"])

	assert result.exit_code == 0
	assert result.stdout.strip() == "", f"TTY 下 stdout 应为空，实际: {result.stdout!r}"


def test_stats_json_path_unchanged(tmp_path):
	"""管道 / --json 路径必须仍是单行 JSON 信封（AC3）。"""
	result = _invoke(tmp_path)

	assert result.exit_code == 0
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["command"] == "stats"
	assert "funnel" in payload["data"]
