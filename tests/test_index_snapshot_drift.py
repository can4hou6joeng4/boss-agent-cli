"""索引缓存元信息漂移按容错口径归一，不把坏类型带进 index_snapshot 信封（Issue #1221、#1224）。"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.index_cache import get_index_info, get_index_snapshot, save_index
from boss_agent_cli.main import cli


_JOBS = [
	{"security_id": "synthetic-1", "job_id": "synthetic-1", "title": "synthetic"},
	{"security_id": "synthetic-2", "job_id": "synthetic-2", "title": "synthetic"},
]


def _write_cache(data_dir: Path, **payload: object) -> None:
	cache = data_dir / "cache"
	cache.mkdir(parents=True, exist_ok=True)
	(cache / "index_cache.json").write_text(json.dumps({"jobs": _JOBS, **payload}), encoding="utf-8")


def _write_raw(data_dir: Path, text: str) -> None:
	cache = data_dir / "cache"
	cache.mkdir(parents=True, exist_ok=True)
	(cache / "index_cache.json").write_text(text, encoding="utf-8")


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


@pytest.mark.parametrize("count", [0, 5])
def test_count_always_follows_validated_jobs(count, tmp_path):
	"""save_index 保证 count == len(jobs)；漂移的 count 会让越界提示自相矛盾。"""
	_write_cache(tmp_path, count=count)

	assert get_index_info(tmp_path)["count"] == len(_JOBS)


@pytest.mark.parametrize("count", [10**400, -1])
def test_count_outside_sane_range_falls_back_to_job_count(count, tmp_path):
	_write_cache(tmp_path, count=count)

	assert get_index_info(tmp_path)["count"] == len(_JOBS)


@pytest.mark.parametrize("page", [10**400, 1_000_001])
def test_page_outside_sane_range_is_none(page, tmp_path):
	_write_cache(tmp_path, page=page)

	assert get_index_info(tmp_path)["page"] is None


def test_page_upper_bound_is_inclusive(tmp_path):
	_write_cache(tmp_path, page=1_000_000)

	assert get_index_info(tmp_path)["page"] == 1_000_000


@pytest.mark.parametrize("saved_at", [-1, -0.5, 10**308, 4_102_444_801.0])
def test_saved_at_outside_sane_window_is_zero(saved_at, tmp_path):
	_write_cache(tmp_path, saved_at=saved_at)

	assert get_index_info(tmp_path)["saved_at"] == 0


def test_saved_at_upper_bound_is_preserved(tmp_path):
	_write_cache(tmp_path, saved_at=4_102_444_800.0)

	assert get_index_info(tmp_path)["saved_at"] == 4_102_444_800.0


def test_unencodable_source_is_emptied(tmp_path):
	# JSON 转义可以产生孤立代理字符：嵌入错误信封后 stdout 编码会失败。
	_write_raw(tmp_path, json.dumps({"jobs": _JOBS, "source": "search:\ud800"}))

	assert get_index_info(tmp_path)["source"] == ""


def test_cache_with_overlong_integer_literal_reports_missing(tmp_path):
	"""Python 3.11+ 的整数字面量上限让 json.loads 抛裸 ValueError，不能兜成网络错误。"""
	_write_raw(tmp_path, '{"jobs": [], "count": ' + "9" * 5000 + "}")

	assert get_index_info(tmp_path) == {"exists": False, "source": "", "page": None, "count": 0, "saved_at": 0}
	assert get_index_snapshot(tmp_path) is None


def test_deeply_nested_cache_never_raises(tmp_path):
	"""深嵌套在 3.11–3.13 抛 RecursionError（按缺失处理），3.14 起解析器不再受限。

	两种解释器行为都必须给出可用的信封数据，不能把解析异常冒泡成网络错误。
	"""
	_write_raw(tmp_path, '{"jobs": [], "count": ' + "[" * 100_000 + "]" * 100_000 + "}")

	info = get_index_info(tmp_path)

	assert set(info) == {"exists", "source", "page", "count", "saved_at"}
	assert info["count"] == 0
	assert info["saved_at"] == 0


def test_show_out_of_range_uses_actual_job_count(tmp_path):
	"""越界提示里的条数必须与可编号的 jobs 一致，否则 Agent 按提示重试必然再失败。"""
	_write_cache(tmp_path, count=5)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "show", "5"])

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "共 2 条结果" in payload["error"]["message"]
