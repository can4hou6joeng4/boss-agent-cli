"""Tests for index_cache.py — save, load, get by index."""

import json

from boss_agent_cli.index_cache import (
	save_index, try_save_index, get_job_by_index, get_index_info,
)


_SAMPLE_JOBS = [
	{
		"security_id": "sid_001",
		"job_id": "jid_001",
		"title": "Go 工程师",
		"company": "Test Corp",
		"salary": "20-30K",
		"city": "广州",
		"experience": "3-5年",
		"education": "本科",
		"skills": ["Go", "Docker"],
		"raw_job_type": 4,
		"employment_type": "实习",
		"days_per_week": "4天/周",
		"least_month": "3个月",
	},
	{
		"security_id": "sid_002",
		"job_id": "jid_002",
		"title": "Python 后端",
		"company": "Another Corp",
		"salary": "15-25K",
		"city": "深圳",
		"experience": "1-3年",
		"education": "本科",
		"skills": ["Python"],
	},
]


class TestSaveIndex:
	def test_creates_file(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		cache_file = tmp_path / "cache" / "index_cache.json"
		assert cache_file.exists()

	def test_content_structure(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS, source="search")
		cache_file = tmp_path / "cache" / "index_cache.json"
		data = json.loads(cache_file.read_text(encoding="utf-8"))
		assert data["source"] == "search"
		assert data["count"] == 2
		assert "saved_at" in data
		assert len(data["jobs"]) == 2

	def test_preserves_fields(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		cache_file = tmp_path / "cache" / "index_cache.json"
		data = json.loads(cache_file.read_text(encoding="utf-8"))
		job = data["jobs"][0]
		assert job["security_id"] == "sid_001"
		assert job["skills"] == ["Go", "Docker"]
		assert job["raw_job_type"] == 4
		assert job["employment_type"] == "实习"
		assert job["days_per_week"] == "4天/周"
		assert job["least_month"] == "3个月"

	def test_legacy_jobs_get_safe_type_defaults(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)

		job = get_job_by_index(tmp_path, 2)

		assert job is not None
		assert job["raw_job_type"] is None
		assert job["employment_type"] == ""
		assert job["days_per_week"] == ""
		assert job["least_month"] == ""


class TestTrySaveIndex:
	def test_success(self, tmp_path):
		assert try_save_index(tmp_path, _SAMPLE_JOBS) is True

	def test_failure_returns_false(self, tmp_path, monkeypatch):
		"""写入失败应返回 False；不用 chmod，避免 Windows 权限语义差异。"""
		from pathlib import Path

		def fail_write_text(self, data, *args, **kwargs):
			raise OSError("simulated write failure")

		monkeypatch.setattr(Path, "write_text", fail_write_text)
		result = try_save_index(tmp_path, _SAMPLE_JOBS)
		assert result is False


class TestGetJobByIndex:
	def test_valid_index(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		job = get_job_by_index(tmp_path, 1)
		assert job is not None
		assert job["title"] == "Go 工程师"

	def test_second_index(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		job = get_job_by_index(tmp_path, 2)
		assert job["title"] == "Python 后端"

	def test_out_of_range(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		assert get_job_by_index(tmp_path, 0) is None
		assert get_job_by_index(tmp_path, 99) is None

	def test_no_cache_file(self, tmp_path):
		assert get_job_by_index(tmp_path, 1) is None

	def test_corrupted_file(self, tmp_path):
		cache_dir = tmp_path / "cache"
		cache_dir.mkdir()
		(cache_dir / "index_cache.json").write_text("NOT JSON")
		assert get_job_by_index(tmp_path, 1) is None


class TestGetIndexInfo:
	def test_with_cache(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS, source="recommend")
		info = get_index_info(tmp_path)
		assert info["exists"] is True
		assert info["source"] == "recommend"
		assert info["count"] == 2
		assert info["saved_at"] > 0

	def test_without_cache(self, tmp_path):
		info = get_index_info(tmp_path)
		assert info["exists"] is False
		assert info["count"] == 0
