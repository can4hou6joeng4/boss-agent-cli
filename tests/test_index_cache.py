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
		data = json.loads(cache_file.read_text())
		assert data["source"] == "search"
		assert data["count"] == 2
		assert "saved_at" in data
		assert len(data["jobs"]) == 2

	def test_preserves_fields(self, tmp_path):
		save_index(tmp_path, _SAMPLE_JOBS)
		cache_file = tmp_path / "cache" / "index_cache.json"
		data = json.loads(cache_file.read_text())
		job = data["jobs"][0]
		assert job["security_id"] == "sid_001"
		assert job["skills"] == ["Go", "Docker"]


class TestTrySaveIndex:
	def test_success(self, tmp_path):
		assert try_save_index(tmp_path, _SAMPLE_JOBS) is True

	def test_failure_returns_false(self, tmp_path):
		# Make cache dir readonly
		cache_dir = tmp_path / "cache"
		cache_dir.mkdir()
		cache_dir.chmod(0o444)
		result = try_save_index(tmp_path, _SAMPLE_JOBS)
		cache_dir.chmod(0o755)  # restore for cleanup
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
