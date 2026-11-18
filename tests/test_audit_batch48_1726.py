"""批次四十八审计修复（Issue #1726）：hr download-resume 的特殊文件输出。

FIFO / 设备 / 符号链接此前会通过扩展名检查，消耗四个平台请求后才被
`O_EXCL` 报成「文件已存在」。现在与 digest / stats / resume export 同口径，
在取凭据和发请求之前按 `INVALID_PARAM` / `LOCAL_IO_ERROR` 拒绝。
"""

import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

from boss_agent_cli.main import cli

_MODULE = "boss_agent_cli.commands.recruiter.download_resume"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "hr", "download-resume", *args])


def _envelope(result) -> dict:
	return json.loads(result.output)


def _make_fifo(path: Path) -> Path:
	os.mkfifo(path)
	return path


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_download_resume_rejects_fifo_before_platform(tmp_path, monkeypatch):
	fifo = _make_fifo(tmp_path / "resume.pdf")
	monkeypatch.setattr(
		f"{_MODULE}.get_recruiter_platform_instance",
		lambda *args, **kwargs: pytest.fail("特殊文件目标不应构造平台实例"),
	)

	result = _invoke(tmp_path, "123", "--message-id", "456", "--output", str(fifo))

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "普通文件" in error["message"]
	assert stat.S_ISFIFO(fifo.lstat().st_mode), "FIFO 不能被替换或删除"


@pytest.mark.skipif(os.name == "nt", reason="符号链接权限依 Windows 配置而定")
def test_download_resume_rejects_symlink_before_platform(tmp_path, monkeypatch):
	target = tmp_path / "existing.pdf"
	target.write_text("old", encoding="utf-8")
	link = tmp_path / "resume.pdf"
	link.symlink_to(target)
	monkeypatch.setattr(
		f"{_MODULE}.get_recruiter_platform_instance",
		lambda *args, **kwargs: pytest.fail("符号链接目标不应构造平台实例"),
	)

	result = _invoke(tmp_path, "123", "--message-id", "456", "--output", str(link))

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert link.is_symlink()
	assert target.read_text(encoding="utf-8") == "old"


def test_download_resume_rejects_parent_not_dir_before_platform(tmp_path, monkeypatch):
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")
	monkeypatch.setattr(
		f"{_MODULE}.get_recruiter_platform_instance",
		lambda *args, **kwargs: pytest.fail("父路径错误不应构造平台实例"),
	)

	result = _invoke(tmp_path, "123", "--message-id", "456", "--output", str(blocker / "resume.pdf"))

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert "父路径" in error["message"]


def test_download_resume_still_writes_regular_file(tmp_path):
	"""普通文件的既有成功路径不变。"""
	from unittest.mock import patch

	with patch(f"{_MODULE}.AuthManager"), patch(f"{_MODULE}.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.return_value = {
			"code": 0,
			"zpData": {"path": str(tmp_path / "resume.pdf"), "bytes": 100, "format": "pdf"},
		}
		platform.is_success.return_value = True
		platform.unwrap_data.side_effect = lambda response: response["zpData"]

		result = _invoke(tmp_path, "123", "--message-id", "456", "--output", str(tmp_path / "resume.pdf"))

	assert result.exit_code == 0, result.output
	assert _envelope(result)["data"]["downloaded"] is True
