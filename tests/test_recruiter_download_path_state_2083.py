"""download_resume_by_friend 的目标路径状态判定（Issue #2083）。

旧实现用 ``output.exists() or output.is_symlink()``：3.14 起这两个方法把权限
等 OSError 吞成 False，存在但读不到的目标文件会绕过覆盖保护，先发出四个平台
请求，最后才在写入阶段失败。新实现用 ``lstat`` 判定：只有 FileNotFoundError
算缺失，元数据错误在发出任何请求之前上抛。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.recruiter_client import BossRecruiterClient


def _client() -> BossRecruiterClient:
	auth = MagicMock()
	auth.get_token.return_value = {
		"cookies": {"wt2": "fake"},
		"stoken": "fake_stoken",
		"user_agent": "TestAgent",
	}
	return BossRecruiterClient(auth)


def _refuse_requests(client: BossRecruiterClient) -> BossRecruiterClient:
	"""目标路径状态不可判定时不应发出任何平台请求。"""

	def _boom(*args: object, **kwargs: object) -> None:
		raise AssertionError("目标路径状态不可判定时不应发出平台请求")

	client.chat_history = _boom  # type: ignore[method-assign]
	return client


def test_existing_file_with_swallowed_metadata_errors_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""exists/is_symlink 吞掉元数据错误时，真实存在的文件仍要被拒绝。"""
	target = tmp_path / "resume.pdf"
	target.write_bytes(b"%PDF-1.4\n")
	# 模拟 3.14 的 Path.exists() / is_symlink()：权限等 OSError 被吞成 False。
	monkeypatch.setattr(Path, "exists", lambda self: False)
	monkeypatch.setattr(Path, "is_symlink", lambda self: False)
	client = _refuse_requests(_client())
	try:
		with pytest.raises(FileExistsError):
			client.download_resume_by_friend(1, 2, target)
	finally:
		client.close()


def test_permission_error_before_any_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""lstat 的权限错误必须在发请求之前上抛，不能被当成「文件不存在」。"""
	target = tmp_path / "resume.pdf"
	monkeypatch.setattr(Path, "exists", lambda self: False)
	monkeypatch.setattr(Path, "is_symlink", lambda self: False)

	def _denied(self: Path) -> None:
		raise PermissionError(13, "Permission denied", str(self))

	monkeypatch.setattr(Path, "lstat", _denied)
	client = _refuse_requests(_client())
	try:
		with pytest.raises(PermissionError):
			client.download_resume_by_friend(1, 2, target)
	finally:
		client.close()


def test_dangling_symlink_is_still_refused(tmp_path: Path) -> None:
	target = tmp_path / "resume.pdf"
	target.symlink_to(tmp_path / "missing.pdf")
	client = _refuse_requests(_client())
	try:
		with pytest.raises(FileExistsError):
			client.download_resume_by_friend(1, 2, target)
	finally:
		client.close()


def test_existing_regular_file_is_refused(tmp_path: Path) -> None:
	target = tmp_path / "resume.pdf"
	target.write_bytes(b"%PDF-1.4\n")
	client = _refuse_requests(_client())
	try:
		with pytest.raises(FileExistsError):
			client.download_resume_by_friend(1, 2, target)
	finally:
		client.close()


def test_missing_target_enters_download_flow(tmp_path: Path) -> None:
	sentinel = RuntimeError("entered download flow")

	def _enter(*args: object, **kwargs: object) -> None:
		raise sentinel

	client = _client()
	client.chat_history = _enter  # type: ignore[method-assign]
	target = tmp_path / "resume.pdf"
	try:
		with pytest.raises(RuntimeError) as excinfo:
			client.download_resume_by_friend(1, 2, target)
		assert excinfo.value is sentinel
	finally:
		client.close()
