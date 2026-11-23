"""doctor 的浏览器缓存探测与 salt 符号链接判定（Issue #2103、#2104）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services import browser_runtime


def _invoke(*args, tmp_path: Path | None = None):
	cli_args = ["--data-dir", str(tmp_path)] if tmp_path is not None else []
	result = CliRunner().invoke(cli, [*cli_args, *args])
	return result, (json.loads(result.stdout) if result.stdout.strip() else None)


def _doctor_checks(tmp_path: Path) -> dict[str, dict]:
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as mock_auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp") as mock_cdp,
		patch("boss_agent_cli.commands.doctor.httpx.get") as mock_httpx,
		patch("boss_agent_cli.commands.doctor.extract_cookies") as mock_cookie,
	):
		mock_auth.return_value.check_status.return_value = None
		mock_cdp.return_value = None
		mock_httpx.return_value = MagicMock(status_code=200)
		mock_cookie.return_value = None
		result, payload = _invoke("--delay", "45-90", "doctor", tmp_path=tmp_path)

	assert result.exit_code == 0, result.output
	assert payload is not None
	return {item["name"]: item for item in payload["data"]["checks"]}


# ── #2103：缓存布局与权限 ───────────────────────────────────────────


def test_xdg_cache_home_is_probed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Linux 的 XDG_CACHE_HOME 布局与驱动同口径探测，不能误报内核缺失。"""
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setattr(browser_runtime.sys, "platform", "linux")
	monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
	monkeypatch.delenv("LOCALAPPDATA", raising=False)

	dirs = browser_runtime.patchright_browser_cache_dirs()

	assert dirs == [tmp_path / "xdg-cache" / "ms-playwright"]


def test_linux_without_xdg_uses_dot_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setattr(browser_runtime.sys, "platform", "linux")
	monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

	assert browser_runtime.patchright_browser_cache_dirs() == [tmp_path / ".cache" / "ms-playwright"]


def test_windows_default_fallback_is_probed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""LOCALAPPDATA 缺失时驱动回退 ~/AppData/Local，探测也要覆盖。"""
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setattr(browser_runtime.sys, "platform", "win32")
	monkeypatch.delenv("LOCALAPPDATA", raising=False)

	dirs = browser_runtime.patchright_browser_cache_dirs()

	assert dirs == [tmp_path / "AppData" / "Local" / "ms-playwright"]


def test_unreadable_cache_root_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""缓存根元数据读不出来（权限）时跳过并单独上报，不能抛到 doctor 顶层。"""
	blocked = tmp_path / "blocked" / "ms-playwright"
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [blocked])
	# 探测先用 lstat 判链接（Issue #2559）。3.12 的 Path.lstat 委托 self.stat，
	# 3.14 起独立实现；只 patch lstat 在两个版本上都覆盖元数据读取。
	real_lstat = Path.lstat

	def fake_lstat(self: Path, *args, **kwargs):
		if self == blocked:
			raise PermissionError(13, "permission denied", str(self))
		return real_lstat(self, *args, **kwargs)

	monkeypatch.setattr(Path, "lstat", fake_lstat)

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [blocked]


def test_unreadable_cache_entries_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""缓存根存在但列不出内容时同样记为不可读。"""
	base = tmp_path / "ms-playwright"
	base.mkdir()
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [base])
	real_listdir = os.listdir

	def fake_listdir(path):
		if Path(path) == base:
			raise PermissionError(13, "permission denied", str(path))
		return real_listdir(path)

	monkeypatch.setattr(browser_runtime.os, "listdir", fake_listdir)

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [base]


def test_doctor_warns_when_browser_cache_is_unreadable(tmp_path: Path) -> None:
	"""缓存不可读时 doctor 提示「无法确认」，而不是引导重装或整份失败。"""
	blocked = tmp_path / "blocked-cache"
	with (
		patch("boss_agent_cli.commands.doctor.installed_browser_dirs", return_value=[]),
		patch("boss_agent_cli.commands.doctor.unreadable_browser_cache_dirs", return_value=[blocked]),
		patch("boss_agent_cli.commands.doctor.patchright_chromium_revision", return_value=None),
	):
		checks = _doctor_checks(tmp_path)

	assert checks["patchright_chromium"]["status"] == "warn"
	assert "无法读取" in checks["patchright_chromium"]["detail"]


# ── #2104：salt 有效符号链接按普通文件处理 ─────────────────────────


def test_doctor_auth_salt_symlink_to_regular_file_is_ok(tmp_path: Path) -> None:
	"""链接到普通文件的 salt 登录路径可以正常使用，不能报 broken 并提示删除。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir(parents=True)
	target = tmp_path / "salt-target"
	target.write_bytes(b"x" * 16)
	os.symlink(target, auth_dir / "salt")

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "ok"
	assert "fail-closed" not in checks["auth_salt"]["detail"]


def test_doctor_auth_salt_dangling_symlink_mentions_missing_target(tmp_path: Path) -> None:
	"""悬空链接仍按损坏拒绝，并说明目标缺失而不是笼统的「符号链接」。"""
	auth_dir = tmp_path / "auth"
	auth_dir.mkdir(parents=True)
	os.symlink(tmp_path / "missing-target", auth_dir / "salt")

	checks = _doctor_checks(tmp_path)

	assert checks["auth_salt"]["status"] == "error"
	assert "目标缺失" in checks["auth_salt"]["detail"]
