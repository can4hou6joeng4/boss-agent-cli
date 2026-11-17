"""clean 不跟随符号链接的回归测试（Issue #1497）。

覆盖三类场景：默认按 mtime 清理、``--privacy`` 显式清理、以及部分完成时
错误信封带回已完成 / 失败项。全部使用 tmp_path 构造真实符号链接，不访问网络。
"""

import errno
import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import clean as clean_module
from boss_agent_cli.main import cli

ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


def _invoke(*args, data_dir):
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(data_dir), *args])
	parsed = json.loads(result.stdout) if result.stdout.strip() else None
	return result.exit_code, parsed


def _symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
	try:
		link.symlink_to(target, target_is_directory=target_is_directory)
	except (NotImplementedError, OSError):
		pytest.skip("当前平台不允许创建符号链接")


def _make_old_file(path: Path, content: str = "[]") -> Path:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	old = time.time() - 31 * 86400
	os.utime(path, (old, old))
	return path


def _result(parsed, target):
	return next(row for row in parsed["data"]["results"] if row["target"] == target)


@pytest.mark.parametrize("directory_name", ["chat-history", "chat-export"])
def test_clean_does_not_follow_symlinked_directory(tmp_path, directory_name):
	"""目录链接指向数据目录外时，按 mtime 清理不得删除链接目标里的文件。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	outside = tmp_path / "outside"
	old_file = _make_old_file(outside / "old.json")
	link = data_dir / directory_name
	_symlink(link, outside, target_is_directory=True)

	code, parsed = _invoke("clean", "--days", "30", data_dir=data_dir)

	assert code == 0
	assert parsed["ok"] is True
	assert old_file.exists()
	assert link.is_symlink()
	target = "聊天快照" if directory_name == "chat-history" else "导出文件"
	row = _result(parsed, target)
	assert row["cleaned"] == 0
	assert row["skipped_symlinks"] == 1
	assert "符号链接" in row["warning"]


def test_clean_unlinks_file_symlink_without_touching_target(tmp_path):
	"""既有的文件链接语义保持不变：只删除链接本身，链接目标不被删除。"""
	data_dir = tmp_path / "data"
	snapshots = data_dir / "chat-history"
	snapshots.mkdir(parents=True)
	outside = _make_old_file(tmp_path / "outside" / "old.json")
	link = snapshots / "linked.json"
	_symlink(link, outside)

	code, parsed = _invoke("clean", "--days", "30", data_dir=data_dir)

	assert code == 0
	assert not link.is_symlink()
	assert not link.exists()
	assert outside.exists()
	assert _result(parsed, "聊天快照")["cleaned"] == 1


def test_clean_privacy_unlinks_session_file_link_without_touching_target(tmp_path):
	"""文件链接沿用既有语义：只删除链接本身，链接目标原样保留。"""
	data_dir = tmp_path / "data"
	auth_dir = data_dir / "auth"
	auth_dir.mkdir(parents=True)
	outside = tmp_path / "outside-session.enc"
	outside.write_text("secret", encoding="utf-8")
	session = auth_dir / "session.enc"
	_symlink(session, outside)

	code, parsed = _invoke("clean", "--privacy", data_dir=data_dir)

	assert code == 0
	assert parsed["ok"] is True
	assert not session.is_symlink()
	assert outside.read_text(encoding="utf-8") == "secret"
	assert _result(parsed, "登录会话")["cleaned"] == 1


def test_clean_privacy_skips_symlinked_targets_without_partial_failure(tmp_path):
	"""``--privacy`` 遇到目录链接时完整结束：删除真实目标，链接与链接内容保留。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	outside = tmp_path / "outside"
	resume_target = _make_old_file(outside / "resumes" / "default.json", "secret")
	history_target = _make_old_file(outside / "history" / "snapshot.json", "secret")
	export_target = _make_old_file(outside / "exports" / "messages.json", "secret")
	for name, target in (
		("resumes", outside / "resumes"),
		("chat-history", outside / "history"),
		("chat-export", outside / "exports"),
	):
		_symlink(data_dir / name, target, target_is_directory=True)
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True)
	session.write_text("secret", encoding="utf-8")

	code, parsed = _invoke("clean", "--privacy", data_dir=data_dir)

	assert code == 0
	assert parsed["ok"] is True
	assert not session.exists()
	for path in (resume_target, history_target, export_target):
		assert path.exists()
	for name in ("resumes", "chat-history", "chat-export"):
		assert (data_dir / name).is_symlink()
	for target in ("本地简历", "聊天快照", "导出文件"):
		assert _result(parsed, target)["skipped_symlinks"] >= 1


def test_clean_privacy_skips_target_under_symlinked_ancestor(tmp_path):
	"""祖先目录是符号链接时同样不跟随：``auth`` 链接到目录外也不删登录态。"""
	data_dir = tmp_path / "data"
	data_dir.mkdir()
	outside_auth = tmp_path / "outside-auth"
	outside_auth.mkdir()
	session = outside_auth / "session.enc"
	session.write_text("secret", encoding="utf-8")
	_symlink(data_dir / "auth", outside_auth, target_is_directory=True)

	code, parsed = _invoke("clean", "--privacy", data_dir=data_dir)

	assert code == 0
	assert parsed["ok"] is True
	assert session.exists()
	assert (data_dir / "auth").is_symlink()
	assert _result(parsed, "登录会话")["skipped_symlinks"] == 1


def test_clean_privacy_preflight_aborts_before_deleting_anything(tmp_path, monkeypatch):
	"""预检发现不可处理目标时，登录态也不能先被删掉。"""
	data_dir = tmp_path / "data"
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True)
	session.write_text("secret", encoding="utf-8")
	resume = data_dir / "resumes" / "default.json"
	resume.parent.mkdir(parents=True)
	resume.write_text("secret", encoding="utf-8")

	def fake_writable(path: Path) -> bool:
		return path != data_dir / "resumes"

	monkeypatch.setattr(clean_module, "_is_writable", fake_writable)

	code, parsed = _invoke("clean", "--privacy", data_dir=data_dir)

	assert code == 1
	assert parsed["ok"] is False
	assert set(parsed) == ENVELOPE_KEYS
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert parsed["data"]["completed"] == []
	assert any(item["target"] == "本地简历" for item in parsed["data"]["failed"])
	assert "本地简历" not in parsed["data"]["remaining"]
	assert session.exists()
	assert resume.exists()


def test_clean_privacy_midway_failure_returns_completed_and_failed(tmp_path, monkeypatch):
	"""删除中途失败时，错误信封仍要带回已完成项与失败项。"""
	data_dir = tmp_path / "data"
	session = data_dir / "auth" / "session.enc"
	session.parent.mkdir(parents=True)
	session.write_text("secret", encoding="utf-8")
	resume = data_dir / "resumes" / "default.json"
	resume.parent.mkdir(parents=True)
	resume.write_text("secret", encoding="utf-8")

	original_clean_path = clean_module._clean_path

	def flaky_clean_path(path, *, dry_run, root=None):
		if path == data_dir / "resumes":
			raise OSError(errno.EACCES, "denied", str(path))
		return original_clean_path(path, dry_run=dry_run, root=root)

	monkeypatch.setattr(clean_module, "_clean_path", flaky_clean_path)

	code, parsed = _invoke("clean", "--privacy", data_dir=data_dir)

	assert code == 1
	assert parsed["ok"] is False
	assert set(parsed) == ENVELOPE_KEYS
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert [item["target"] for item in parsed["data"]["completed"]] == ["登录会话"]
	assert parsed["data"]["failed"][0]["target"] == "本地简历"
	assert "本地简历" not in parsed["data"]["remaining"]
	assert not session.exists()
	assert resume.exists()


def test_clean_still_deletes_expired_real_snapshot(tmp_path):
	"""正常目录的清理路径保持不变。"""
	data_dir = tmp_path / "data"
	old = _make_old_file(data_dir / "chat-history" / "old.json")
	fresh = data_dir / "chat-history" / "fresh.json"
	fresh.write_text("[]", encoding="utf-8")

	code, parsed = _invoke("clean", "--days", "30", data_dir=data_dir)

	assert code == 0
	assert not old.exists()
	assert fresh.exists()
	assert _result(parsed, "聊天快照")["cleaned"] == 1
	assert _result(parsed, "聊天快照")["skipped_symlinks"] == 0
