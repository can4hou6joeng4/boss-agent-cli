"""批次五十一审计修复（Issue #1732）：索引缓存按路径状态读取。

`_load_cache()` 原来用 `exists()` + `read_text()`：FIFO 会永久阻塞
`boss show`，元数据错误又被当成「没有缓存」并提示重跑搜索。
现在与 stats 缓存库（#1716）同口径：只有 FileNotFoundError 才算没有缓存。
"""

import errno
import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.index_cache import get_index_info, get_job_by_index, get_index_snapshot
from boss_agent_cli.main import cli


def _cache_path(data_dir: Path) -> Path:
	return data_dir / "cache" / "index_cache.json"


def _seed(data_dir: Path, jobs: list[dict]) -> Path:
	path = _cache_path(data_dir)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps({"source": "search", "page": 1, "jobs": jobs}), encoding="utf-8")
	return path


def test_missing_cache_is_still_absent(tmp_path):
	assert get_index_info(tmp_path)["exists"] is False
	assert get_job_by_index(tmp_path, 1) is None
	assert get_index_snapshot(tmp_path) is None


def test_regular_cache_still_reads(tmp_path):
	_seed(tmp_path, [{"security_id": "sid-1", "title": "后端工程师"}])

	info = get_index_info(tmp_path)

	assert info["exists"] is True
	assert info["count"] == 1
	assert get_job_by_index(tmp_path, 1) == {"security_id": "sid-1", "title": "后端工程师"}


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_fifo_cache_fails_closed_instead_of_blocking(tmp_path):
	path = _cache_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	os.mkfifo(path)

	with pytest.raises(OSError) as exc_info:
		get_index_info(tmp_path)

	assert str(path) == exc_info.value.filename
	assert stat.S_ISFIFO(path.lstat().st_mode), "FIFO 不能被替换或删除"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_show_reports_local_io_for_fifo_cache(tmp_path):
	path = _cache_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	os.mkfifo(path)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "show", "1"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert "没有缓存的搜索结果" not in payload["error"]["message"]


def test_dangling_symlink_cache_fails_closed(tmp_path):
	path = _cache_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		path.symlink_to(tmp_path / "missing.json")
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	with pytest.raises(OSError):
		get_index_info(tmp_path)


def test_symlink_to_regular_cache_still_reads(tmp_path):
	real = tmp_path / "real-index.json"
	real.write_text(json.dumps({"jobs": [{"security_id": "sid-1"}]}), encoding="utf-8")
	path = _cache_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		path.symlink_to(real)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	assert get_index_info(tmp_path)["count"] == 1


def test_metadata_error_is_not_reported_as_no_cache(tmp_path, monkeypatch):
	"""lstat 失败（如权限）不能退化成「没有缓存」（Issue #1732）。"""
	_seed(tmp_path, [{"security_id": "sid-1"}])
	path = _cache_path(tmp_path)
	real_lstat = Path.lstat

	def lstat_fails(self: Path):
		if self == path:
			raise PermissionError(errno.EACCES, "Permission denied", str(self))
		return real_lstat(self)

	monkeypatch.setattr(Path, "lstat", lstat_fails)

	with pytest.raises(OSError):
		get_index_info(tmp_path)


def test_corrupt_cache_json_still_reads_as_absent(tmp_path):
	path = _cache_path(tmp_path)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("{not json", encoding="utf-8")

	assert get_index_info(tmp_path)["exists"] is False
