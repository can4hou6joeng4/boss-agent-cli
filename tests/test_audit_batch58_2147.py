"""审计批次五十八：cache 读取边界的三处漏解码（Issue #2147）。

#1885 / #1964 / #2056 已给大部分 TEXT / REAL 列加了读取边界解码，但
``shortlist_records.created_at``、``saved_searches.name``、
``resume_job_links.linked_at/updated_at`` 仍原样透传漂移值：BLOB 进入
data 后序列化兜成固定 INTERNAL_ERROR，saved_searches 的坏行还会绕过
corrupt 名单。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
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


def _seed_shortlist(data_dir: Path) -> None:
	result = _invoke(
		data_dir, "shortlist", "add", "S1", "J1", "--title", "后端开发", "--company", "TestCo"
	)
	assert result.exit_code == 0, result.stdout


# shortlist_records.created_at


def test_shortlist_created_at_blob_is_skipped_with_corrupt_list(tmp_path) -> None:
	"""REAL 时间戳漂移成 BLOB 时跳过并回报，不再让整个候选池不可见（Issue #2224）。"""
	_seed_shortlist(tmp_path)
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE shortlist_records SET created_at = X'FF'")

	with CacheStore(_db(tmp_path)) as store:
		items, corrupt = store.list_shortlist_with_corrupt()

	assert items == []
	assert corrupt == ["J1"]


def test_shortlist_list_reports_corrupt_entries(tmp_path) -> None:
	_seed_shortlist(tmp_path)
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE shortlist_records SET created_at = X'414243'")

	result = _invoke(tmp_path, "shortlist", "list")

	payload = _envelope(result)
	assert result.exit_code == 0
	assert payload["ok"] is True
	assert payload["data"] == []
	assert payload["hints"]["corrupt"] == ["J1"]


# ── saved_searches.name ──────────────────────────────────────────────


def test_saved_search_blob_name_is_decoded(tmp_path) -> None:
	"""全表扫描路径把有效 UTF-8 的 BLOB name 还原成文本（Issue #2147）。"""
	with CacheStore(_db(tmp_path)) as store:
		store.save_saved_search("n1", {"query": "q"})
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE saved_searches SET name = X'414243'")

	with CacheStore(_db(tmp_path)) as store:
		items, corrupt = store.list_saved_searches_with_corrupt()

	assert [entry["name"] for entry in items] == ["ABC"]
	assert corrupt == []


def test_get_saved_search_returns_text_name(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		store.save_saved_search("n1", {"query": "q"})

	with CacheStore(_db(tmp_path)) as store:
		item = store.get_saved_search("n1")

	assert item is not None
	assert item["name"] == "n1"


def test_saved_search_invalid_blob_name_goes_to_corrupt(tmp_path) -> None:
	"""无效 UTF-8 的 name 行计入 corrupt 并跳过，其余预设照常返回。"""
	with CacheStore(_db(tmp_path)) as store:
		store.save_saved_search("n1", {"query": "q"})
		store.save_saved_search("n2", {"query": "q2"})
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE saved_searches SET name = X'FF' WHERE name = 'n1'")

	with CacheStore(_db(tmp_path)) as store:
		items, corrupt = store.list_saved_searches_with_corrupt()

	assert [entry["name"] for entry in items] == ["n2"]
	assert len(corrupt) == 1


def test_preset_list_survives_invalid_blob_name(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		store.save_saved_search("n1", {"query": "q"})
		store.save_saved_search("n2", {"query": "q2"})
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE saved_searches SET name = X'FF' WHERE name = 'n1'")

	result = _invoke(tmp_path, "preset", "list")

	payload = _envelope(result)
	assert result.exit_code == 0, result.stdout
	assert [entry["name"] for entry in payload["data"]] == ["n2"]
	assert len(payload["hints"]["corrupt"]) == 1


# ── resume_job_links.linked_at / updated_at ──────────────────────────


def test_resume_link_blob_timestamps_are_rejected(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as store:
		store.link_resume_to_job("cv.pdf", "S1", "J1", "后端开发", "TestCo")
	with sqlite3.connect(_db(tmp_path)) as conn:
		conn.execute("UPDATE resume_job_links SET linked_at = X'FF', updated_at = X'FF'")

	with CacheStore(_db(tmp_path)) as store:
		with pytest.raises(CacheRecordInvalid) as excinfo:
			store.get_resume_applications("cv.pdf")

	assert excinfo.value.column == "linked_at"
