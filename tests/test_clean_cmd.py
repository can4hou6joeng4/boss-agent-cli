"""clean 命令测试 — 覆盖缓存清理的各种场景。"""

import json
import sqlite3
import time

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(*args, tmp_path=None):
	runner = CliRunner()
	cli_args = []
	if tmp_path is not None:
		cli_args.extend(["--data-dir", str(tmp_path)])
	cli_args.extend(args)
	result = runner.invoke(cli, cli_args)
	return result.exit_code, json.loads(result.output) if result.output.strip() else None


def _setup_db(tmp_path, *, expired_search=0, fresh_search=0, greets=0, applies=0):
	"""在临时目录中创建测试数据库。"""
	db_dir = tmp_path / "cache"
	db_dir.mkdir(parents=True, exist_ok=True)
	db_path = db_dir / "boss_agent.db"
	conn = sqlite3.connect(str(db_path))
	conn.executescript("""
		CREATE TABLE IF NOT EXISTS search_cache (cache_key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at REAL NOT NULL);
		CREATE TABLE IF NOT EXISTS greet_records (security_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, greeted_at REAL NOT NULL);
		CREATE TABLE IF NOT EXISTS apply_records (security_id TEXT NOT NULL, job_id TEXT NOT NULL, applied_at REAL NOT NULL, PRIMARY KEY (security_id, job_id));
	""")
	now = time.time()
	for i in range(expired_search):
		conn.execute("INSERT INTO search_cache VALUES (?, ?, ?)", (f"expired_{i}", f"resp_{i}", now - 100000))
	for i in range(fresh_search):
		conn.execute("INSERT INTO search_cache VALUES (?, ?, ?)", (f"fresh_{i}", f"resp_{i}", now))
	for i in range(greets):
		conn.execute("INSERT INTO greet_records VALUES (?, ?, ?)", (f"s{i}", f"j{i}", now))
	for i in range(applies):
		conn.execute("INSERT INTO apply_records VALUES (?, ?, ?)", (f"s{i}", f"j{i}", now))
	conn.commit()
	conn.close()
	return db_path


# ── 基本功能 ────────────────────────────────────────────────────────


def test_clean_empty_data_dir(tmp_path):
	"""空数据目录不应报错。"""
	code, parsed = _invoke("clean", tmp_path=tmp_path)
	assert code == 0
	assert parsed["ok"] is True
	assert parsed["data"]["total_bytes_freed"] == 0


def test_clean_dry_run_does_not_delete(tmp_path):
	"""预览模式不应实际删除数据。"""
	_setup_db(tmp_path, expired_search=5)
	code, parsed = _invoke("clean", "--dry-run", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["dry_run"] is True
	# 搜索缓存应报告 5 条可清理
	search_result = next(r for r in parsed["data"]["results"] if r["target"] == "搜索缓存")
	assert search_result["cleaned"] == 5
	# 但数据库中数据应仍在
	conn = sqlite3.connect(str(tmp_path / "cache" / "boss_agent.db"))
	count = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
	conn.close()
	assert count == 5


def test_clean_deletes_expired_search_cache(tmp_path):
	"""正常模式应删除过期搜索缓存。"""
	_setup_db(tmp_path, expired_search=3, fresh_search=2)
	code, parsed = _invoke("clean", tmp_path=tmp_path)
	assert code == 0
	search_result = next(r for r in parsed["data"]["results"] if r["target"] == "搜索缓存")
	assert search_result["cleaned"] == 3
	# 新鲜缓存应保留
	conn = sqlite3.connect(str(tmp_path / "cache" / "boss_agent.db"))
	count = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
	conn.close()
	assert count == 2


def test_clean_all_deletes_everything(tmp_path):
	"""全量清理应删除所有缓存和记录。"""
	_setup_db(tmp_path, expired_search=2, fresh_search=3, greets=5, applies=2)
	code, parsed = _invoke("clean", "--all", tmp_path=tmp_path)
	assert code == 0
	search_result = next(r for r in parsed["data"]["results"] if r["target"] == "搜索缓存")
	assert search_result["cleaned"] == 5  # 全部
	greet_result = next(r for r in parsed["data"]["results"] if r["target"] == "打招呼记录")
	assert greet_result["cleaned"] == 5
	apply_result = next(r for r in parsed["data"]["results"] if r["target"] == "投递记录")
	assert apply_result["cleaned"] == 2


# ── 索引缓存 ────────────────────────────────────────────────────────


def test_clean_index_cache(tmp_path):
	"""应清理索引缓存文件。"""
	cache_dir = tmp_path / "cache"
	cache_dir.mkdir(parents=True, exist_ok=True)
	idx = cache_dir / "index_cache.json"
	idx.write_text('{"test": 1}', encoding="utf-8")
	code, parsed = _invoke("clean", tmp_path=tmp_path)
	assert code == 0
	idx_result = next(r for r in parsed["data"]["results"] if r["target"] == "索引缓存")
	assert idx_result["cleaned"] == 1
	assert not idx.exists()


# ── 文件清理 ────────────────────────────────────────────────────────


def test_clean_old_snapshots(tmp_path):
	"""应清理超过指定天数的快照文件。"""
	snap_dir = tmp_path / "chat-history"
	snap_dir.mkdir(parents=True, exist_ok=True)
	# 创建旧文件
	import os
	old_file = snap_dir / "old.json"
	old_file.write_text("[]", encoding="utf-8")
	old_time = time.time() - 31 * 86400
	os.utime(old_file, (old_time, old_time))
	# 创建新文件
	new_file = snap_dir / "new.json"
	new_file.write_text("[]", encoding="utf-8")
	code, parsed = _invoke("clean", "--days", "30", tmp_path=tmp_path)
	assert code == 0
	snap_result = next(r for r in parsed["data"]["results"] if r["target"] == "聊天快照")
	assert snap_result["cleaned"] == 1
	assert not old_file.exists()
	assert new_file.exists()


# ── JSON 信封 ──────────────────────────────────────────────────────


def test_clean_json_envelope(tmp_path):
	"""验证输出符合 JSON 信封规范。"""
	code, parsed = _invoke("clean", tmp_path=tmp_path)
	assert "ok" in parsed
	assert "command" in parsed
	assert parsed["command"] == "clean"
	assert "data" in parsed
	assert "results" in parsed["data"]
	assert "total_bytes_freed" in parsed["data"]
	assert "total_bytes_freed_display" in parsed["data"]


def test_clean_hints_after_dry_run(tmp_path):
	"""预览模式后应提示执行实际清理。"""
	code, parsed = _invoke("clean", "--dry-run", tmp_path=tmp_path)
	actions = parsed.get("hints", {}).get("next_actions", [])
	assert any("clean" in a for a in actions)


def test_clean_hints_after_actual(tmp_path):
	"""实际清理后应提示检查环境。"""
	code, parsed = _invoke("clean", tmp_path=tmp_path)
	actions = parsed.get("hints", {}).get("next_actions", [])
	assert any("doctor" in a for a in actions)


def test_clean_privacy_dry_run_does_not_delete_sensitive_files(tmp_path):
	"""隐私清理预览应报告敏感文件但不删除。"""
	session = tmp_path / "auth" / "session.enc"
	resume = tmp_path / "resumes" / "default.json"
	export = tmp_path / "chat-export" / "messages.json"
	for file in (session, resume, export):
		file.parent.mkdir(parents=True, exist_ok=True)
		file.write_text("secret", encoding="utf-8")

	code, parsed = _invoke("clean", "--privacy", "--dry-run", tmp_path=tmp_path)

	assert code == 0
	targets = {r["target"]: r for r in parsed["data"]["results"]}
	assert targets["登录会话"]["cleaned"] == 1
	assert targets["本地简历"]["cleaned"] == 1
	assert targets["导出文件"]["cleaned"] >= 1
	assert session.exists()
	assert resume.exists()
	assert export.exists()


def test_clean_privacy_deletes_sensitive_files_and_hints_relogin(tmp_path):
	"""显式隐私清理应删除登录会话、本地简历和导出/快照数据。"""
	files = [
		tmp_path / "auth" / "session.enc",
		tmp_path / "resumes" / "default.json",
		tmp_path / "chat-history" / "snapshot.json",
		tmp_path / "chat-export" / "messages.json",
	]
	for file in files:
		file.parent.mkdir(parents=True, exist_ok=True)
		file.write_text("secret", encoding="utf-8")

	code, parsed = _invoke("clean", "--privacy", tmp_path=tmp_path)

	assert code == 0
	assert all(not file.exists() for file in files)
	actions = parsed.get("hints", {}).get("next_actions", [])
	assert any("login" in a for a in actions)
