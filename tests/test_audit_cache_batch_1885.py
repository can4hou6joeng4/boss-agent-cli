"""缓存读取边界、写入拒绝分类与去重迁移（Issue #1885 #1886 #1887）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore, CacheWriteRejected, _decode_text_column
from boss_agent_cli.main import cli

_ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


def _db(data_dir: Path) -> Path:
	return data_dir / "cache" / "boss_agent.db"


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def _envelope(result) -> dict:
	payload = json.loads(result.stdout)
	assert set(payload) == _ENVELOPE_KEYS
	return payload


# ── Issue #1885：TEXT 列的类型漂移在读取边界拦下 ─────────────────────


def test_decode_text_accepts_str_and_utf8_blob() -> None:
	assert _decode_text_column("shortlist", "r1", "title", "后端开发") == "后端开发"
	assert _decode_text_column("shortlist", "r1", "title", "ABC".encode()) == "ABC"


@pytest.mark.parametrize("raw", [b"\xff\xfe\x00", 12, 1.5, True])
def test_decode_text_rejects_drifted_values(raw: object) -> None:
	with pytest.raises(CacheRecordInvalid) as excinfo:
		_decode_text_column("shortlist", "r1", "title", raw)

	assert excinfo.value.column == "title"


def test_decode_text_allows_null_only_when_asked() -> None:
	assert _decode_text_column("shortlist", "r1", "note", None, allow_none=True) is None
	with pytest.raises(CacheRecordInvalid):
		_decode_text_column("shortlist", "r1", "note", None)


def _seed_shortlist(data_dir: Path) -> None:
	result = _invoke(data_dir, "shortlist", "add", "S1", "J1", "--title", "后端开发", "--company", "TestCo")
	assert result.exit_code == 0, result.stdout


def test_shortlist_blob_text_is_decoded(tmp_path) -> None:
	_seed_shortlist(tmp_path)
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE shortlist_records SET title = X'414243'")

	with CacheStore(_db(tmp_path)) as store:
		items = store.list_shortlist()

	assert items[0]["title"] == "ABC"


def test_shortlist_invalid_blob_fails_as_record_invalid(tmp_path) -> None:
	_seed_shortlist(tmp_path)
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE shortlist_records SET title = X'FF'")

	with CacheStore(_db(tmp_path)) as store:
		with pytest.raises(CacheRecordInvalid) as excinfo:
			store.list_shortlist()

	assert excinfo.value.column == "title"


def test_cli_reports_cache_record_invalid_without_internal_error(tmp_path) -> None:
	"""旧输出：固定 INTERNAL_ERROR 且 exit 0 / ok=false；现在按记录损坏上报。"""
	_seed_shortlist(tmp_path)
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE shortlist_records SET title = X'FF'")

	result = _invoke(tmp_path, "shortlist", "list")

	payload = _envelope(result)
	assert result.exit_code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert payload["error"]["recoverable"] is True
	assert "重新创建任务" in payload["error"]["recovery_action"]


def test_workflow_run_text_columns_are_decoded_or_rejected(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		store.create_workflow_run(
			"w1",
			role="recruiter",
			platform="zhipin",
			goal="reply",
			mode="headless",
			params={},
			steps=("recruiter_reply",),
		)

	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE workflow_runs SET goal = X'414243'")

	with CacheStore(_db(tmp_path)) as store:
		assert store.get_workflow_run("w1")["goal"] == "ABC"

	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE workflow_steps SET status = X'FF'")

	with CacheStore(_db(tmp_path)) as store:
		with pytest.raises(CacheRecordInvalid) as excinfo:
			store.list_workflow_steps("w1")

	assert excinfo.value.column == "status"


# ── Issue #1886：约束冲突不是缓存库损坏 ──────────────────────────────


def test_duplicate_workflow_run_is_write_rejected_not_database_invalid(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		kwargs = dict(
			role="recruiter",
			platform="zhipin",
			goal="reply",
			mode="headless",
			params={},
			steps=("recruiter_reply",),
		)
		store.create_workflow_run("w1", **kwargs)

		with pytest.raises(CacheWriteRejected) as excinfo:
			store.create_workflow_run("w1", **kwargs)

		assert "UNIQUE constraint failed" in str(excinfo.value)
		# 库本身仍完全可用：旧数据不能被当成损坏。
		assert store.get_workflow_run("w1") is not None
		assert store.list_workflow_runs()


def test_shortlist_not_null_violation_is_write_rejected(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		with pytest.raises(CacheWriteRejected):
			store.add_shortlist({"security_id": "s1", "job_id": "j1", "title": None})

		assert store.list_shortlist() == []


def test_cli_reports_write_rejected_as_invalid_param(tmp_path, monkeypatch) -> None:
	"""约束冲突给 INVALID_PARAM 指引，绝不引导用户删除缓存库。"""
	def _reject(self, item, *args, **kwargs):
		raise CacheWriteRejected(Path("cache/boss_agent.db"), "UNIQUE constraint failed: shortlist_records")

	monkeypatch.setattr(CacheStore, "add_shortlist", _reject)

	result = _invoke(tmp_path, "shortlist", "add", "S1", "J1")

	payload = _envelope(result)
	assert result.exit_code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "无需删除" in payload["error"]["recovery_action"]
	assert "删除该文件" not in payload["error"]["recovery_action"]


# ── Issue #1887：迁移保留 job_id 为空的旧去重记录 ────────────────────


def _legacy_db(data_dir: Path) -> Path:
	data_dir.mkdir(parents=True, exist_ok=True)
	(data_dir / "cache").mkdir(exist_ok=True)
	path = _db(data_dir)
	with sqlite3.connect(path) as conn:
		conn.execute(
			"CREATE TABLE greet_records ("
			"security_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, greeted_at REAL NOT NULL)"
		)
		conn.execute(
			"CREATE TABLE apply_records ("
			"security_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, applied_at REAL NOT NULL)"
		)
		conn.execute("INSERT INTO greet_records (security_id, job_id, greeted_at) VALUES ('S_LEGACY', '', 111.0)")
		conn.execute("INSERT INTO greet_records (security_id, job_id, greeted_at) VALUES ('S_KEEP', 'J_LEGACY', 222.0)")
		conn.execute("INSERT INTO apply_records (security_id, job_id, applied_at) VALUES ('S_APPLIED', '', 333.0)")
	return path


def test_migration_keeps_empty_job_id_greet_records(tmp_path) -> None:
	path = _legacy_db(tmp_path)

	with CacheStore(path) as store:
		assert store.is_greeted(security_id="S_LEGACY") is True
		assert store.get_job_id("S_LEGACY") == ""
		assert store.is_greeted(security_id="S_KEEP") is True

	with sqlite3.connect(path) as conn:
		rows = conn.execute("SELECT job_id, security_id FROM greet_records ORDER BY security_id").fetchall()

	assert rows == [("J_LEGACY", "S_KEEP"), ("", "S_LEGACY")]


def test_migration_keeps_empty_job_id_apply_records(tmp_path) -> None:
	path = _legacy_db(tmp_path)

	with CacheStore(path) as store:
		assert store.is_applied("S_APPLIED", "") is True

	with sqlite3.connect(path) as conn:
		rows = conn.execute("SELECT job_id, security_id FROM apply_records").fetchall()

	assert rows == [("", "S_APPLIED")]
