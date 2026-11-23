"""浏览器缓存探测：不可读 / 悬空 / 解析失败的缓存根与驱动环境回退（Issue #2559）。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from boss_agent_cli.services import browser_runtime

_CANNOT_MAKE_UNREADABLE = os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0


def _complete_install(root: Path, name: str = "chromium-1248") -> Path:
	entry = root / name
	entry.mkdir(parents=True)
	(entry / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
	return entry


@pytest.mark.skipif(_CANNOT_MAKE_UNREADABLE, reason="当前平台无法用权限位制造不可读目录")
def test_unlistable_cache_root_is_unreadable_not_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Path.glob 对不可读目录静默返回空；探测改用 listdir 后要报「无法确认」。"""
	cache = tmp_path / "ms-playwright"
	_complete_install(cache)
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [cache])
	cache.chmod(0o000)
	try:
		assert browser_runtime.installed_browser_dirs() == []
		assert browser_runtime.unreadable_browser_cache_dirs() == [cache]
	finally:
		cache.chmod(0o755)


def test_cache_root_resolution_failure_is_reported_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
	"""cwd 已删除 / HOME 无法解析时不能落成「未安装」并引导重装。"""

	def fail() -> list[Path]:
		raise FileNotFoundError(2, "no such file or directory")

	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", fail)

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [browser_runtime._CACHE_ROOT_UNKNOWN]


def test_dangling_symlink_cache_root_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	base = tmp_path / "ms-playwright"
	os.symlink(tmp_path / "missing-target", base)
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [base])

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == [base]


def test_symlink_to_complete_install_stays_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	real_dir = _complete_install(tmp_path / "real-cache")
	linked_root = tmp_path / "ms-playwright"
	os.symlink(real_dir.parent, linked_root)
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [linked_root])

	assert browser_runtime.installed_browser_dirs() == [linked_root / real_dir.name]
	assert browser_runtime.unreadable_browser_cache_dirs() == []


def test_cache_root_vanishing_after_stat_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""stat 成功、listdir 前目录被删的竞态与开头的缺失分支同口径，不误报不可读。"""
	base = tmp_path / "ms-playwright"
	base.mkdir()
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [base])
	real_listdir = os.listdir

	def vanished(path):
		if Path(path) == base:
			raise FileNotFoundError(2, "no such file or directory", str(path))
		return real_listdir(path)

	monkeypatch.setattr(browser_runtime.os, "listdir", vanished)

	assert browser_runtime.installed_browser_dirs() == []
	assert browser_runtime.unreadable_browser_cache_dirs() == []


@pytest.mark.parametrize("variable", ["npm_config_playwright_browsers_path", "npm_package_config_playwright_browsers_path"])
def test_npm_config_fallback_matches_driver(variable: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""驱动经 getFromENV 回退 npm_config_*；探测必须与驱动同根（coreBundle.js）。"""
	target = tmp_path / "npm-cache"
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.delenv("npm_config_playwright_browsers_path", raising=False)
	monkeypatch.delenv("npm_package_config_playwright_browsers_path", raising=False)
	monkeypatch.setenv(variable, str(target))

	assert browser_runtime.patchright_browser_cache_dirs() == [target]


def test_playwright_browsers_path_precedes_npm_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	direct = tmp_path / "direct"
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(direct))
	monkeypatch.setenv("npm_config_playwright_browsers_path", str(tmp_path / "npm"))

	assert browser_runtime.patchright_browser_cache_dirs() == [direct]


def test_empty_playwright_browsers_path_shadows_npm_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""驱动只在变量未定义时才回退；显式空串按默认缓存根处理。"""
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "")
	monkeypatch.setenv("npm_config_playwright_browsers_path", str(tmp_path / "npm"))
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setattr(browser_runtime.sys, "platform", "darwin")

	assert browser_runtime.patchright_browser_cache_dirs() == [tmp_path / "Library" / "Caches" / "ms-playwright"]


def test_relative_npm_config_path_uses_init_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.setenv("npm_config_playwright_browsers_path", "relative-cache")
	monkeypatch.setenv("INIT_CWD", str(tmp_path))

	assert browser_runtime.patchright_browser_cache_dirs() == [tmp_path / "relative-cache"]


def test_init_cwd_follows_driver_env_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""INIT_CWD 也经 getFromENV：npm_config_init_cwd 是驱动认的第二个来源。"""
	monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	monkeypatch.setenv("npm_config_playwright_browsers_path", "relative-cache")
	monkeypatch.delenv("INIT_CWD", raising=False)
	monkeypatch.setenv("npm_config_init_cwd", str(tmp_path))

	assert browser_runtime.patchright_browser_cache_dirs() == [tmp_path / "relative-cache"]


def test_zero_value_still_uses_package_local_browsers(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("npm_config_playwright_browsers_path", "0")

	dirs = browser_runtime.patchright_browser_cache_dirs()

	assert len(dirs) == 1
	assert dirs[0].name == ".local-browsers"
	assert dirs[0].parent.name == "package"


def test_unreadable_root_evaluates_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""不可读缓存根不能参与「已安装」判定，评估结果需让 doctor 继续走不可读分支。"""
	base = tmp_path / "ms-playwright"
	base.mkdir()
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [base])
	real_listdir = os.listdir

	def denied(path):
		if Path(path) == base:
			raise PermissionError(13, "permission denied", str(path))
		return real_listdir(path)

	monkeypatch.setattr(browser_runtime.os, "listdir", denied)
	status, detail = browser_runtime.evaluate_patchright_chromium("1248", browser_runtime.installed_browser_dirs())
	assert status != "ok"
	assert "本机缓存仅有：无" in detail


def test_non_directory_cache_root_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	base = tmp_path / "ms-playwright"
	base.write_text("not a directory", encoding="utf-8")
	monkeypatch.setattr(browser_runtime, "patchright_browser_cache_dirs", lambda: [base])

	assert browser_runtime.unreadable_browser_cache_dirs() == [base]
	assert stat.S_ISDIR(base.lstat().st_mode) is False
