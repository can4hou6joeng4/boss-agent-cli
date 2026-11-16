"""索引缓存元信息漂移按容错口径归一，不把坏类型带进 index_snapshot 信封（Issue #1221）。"""

import json
from pathlib import Path

import pytest

from boss_agent_cli.index_cache import get_index_info, get_index_snapshot, save_index


_JOBS = [
	{"security_id": "synthetic-1", "job_id": "synthetic-1", "title": "synthetic"},
	{"security_id": "synthetic-2", "job_id": "synthetic-2", "title": "synthetic"},
]


def _write_cache(data_dir: Path, **payload: object) -> None:
	cache = data_dir / "cache"
	cache.mkdir(parents=True, exist_ok=True)
	(cache / "index_cache.json").write_text(json.dumps({"jobs": _JOBS, **payload}), encoding="utf-8")


def test_drifted_metadata_is_normalized(tmp_path):
	_write_cache(tmp_path, source=123, page="p2", count="many", saved_at="yesterday")

	assert get_index_info(tmp_path) == {"exists": True, "source": "", "page": None, "count": len(_JOBS), "saved_at": 0}
	assert get_index_snapshot(tmp_path) == {"source": "", "page": None, "count": len(_JOBS), "saved_at": 0}


@pytest.mark.parametrize("saved_at", [float("inf"), float("-inf"), float("nan"), "yesterday", None, True, 10**400])
def test_non_finite_saved_at_is_zero(saved_at, tmp_path):
	_write_cache(tmp_path, source="search", saved_at=saved_at)

	info = get_index_info(tmp_path)

	assert info["saved_at"] == 0
	# 其余元信息不受影响：坏时间戳不能连带丢弃可用的编号快照。
	assert info["count"] == len(_JOBS)


@pytest.mark.parametrize("count", ["many", True, -1, None, 7.0])
def test_invalid_count_falls_back_to_job_count(count, tmp_path):
	_write_cache(tmp_path, count=count)

	assert get_index_info(tmp_path)["count"] == len(_JOBS)


@pytest.mark.parametrize("page", ["p2", True, 0, -1, 1.0])
def test_invalid_page_is_none(page, tmp_path):
	_write_cache(tmp_path, page=page)

	assert get_index_info(tmp_path)["page"] is None


@pytest.mark.parametrize("source", [123, None, ["search"], {"name": "search"}])
def test_invalid_source_is_empty(source, tmp_path):
	_write_cache(tmp_path, source=source)

	assert get_index_info(tmp_path)["source"] == ""


def test_valid_metadata_is_preserved(tmp_path):
	save_index(tmp_path, _JOBS, source="search", page=2)

	info = get_index_info(tmp_path)

	assert info["exists"] is True
	assert info["source"] == "search"
	assert info["page"] == 2
	assert info["count"] == len(_JOBS)
	assert isinstance(info["saved_at"], float) and info["saved_at"] > 0


def test_old_cache_without_metadata_keeps_existing_semantics(tmp_path):
	# 旧版本缓存没有 page / source 等字段：页码按 None、条数按实际 jobs。
	_write_cache(tmp_path)

	assert get_index_info(tmp_path) == {"exists": True, "source": "", "page": None, "count": len(_JOBS), "saved_at": 0}


def test_structurally_broken_cache_still_reports_missing(tmp_path):
	cache = tmp_path / "cache"
	cache.mkdir(parents=True)
	(cache / "index_cache.json").write_text(json.dumps({"jobs": "synthetic", "count": 3}), encoding="utf-8")

	assert get_index_info(tmp_path) == {"exists": False, "source": "", "page": None, "count": 0, "saved_at": 0}
	assert get_index_snapshot(tmp_path) is None
