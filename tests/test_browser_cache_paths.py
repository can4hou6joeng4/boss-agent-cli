"""内核预检须遵循 patchright 实际使用的缓存配置（Issue #1073）。"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from boss_agent_cli.services import browser_runtime
from boss_agent_cli.wizard.preflight import _browser_kernel_status


@pytest.mark.parametrize("configured", [None, ""])
@pytest.mark.parametrize(
	("platform", "expected"),
	[
		("linux", Path(".cache") / "ms-playwright"),
		("darwin", Path("Library") / "Caches" / "ms-playwright"),
		("win32", Path("AppData") / "Local" / "ms-playwright"),
	],
)
def test_default_cache_paths_follow_driver_platform_root(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
	configured: str | None,
	platform: str,
	expected: Path,
) -> None:
	"""驱动只读取当前平台的那一个默认缓存根（Issue #2318）。"""
	if configured is None:
		monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
	else:
		monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", configured)
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setattr(browser_runtime.sys, "platform", platform)
	monkeypatch.delenv("LOCALAPPDATA", raising=False)
	monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

	assert browser_runtime.patchright_browser_cache_dirs() == [tmp_path / expected]


def test_explicit_cache_is_the_only_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	cache = tmp_path / "custom-cache"
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))

	assert browser_runtime.patchright_browser_cache_dirs() == [cache]


@pytest.mark.parametrize("init_cwd", [None, "project"])
def test_relative_cache_uses_driver_working_directory(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_cwd: str | None,
) -> None:
	monkeypatch.chdir(tmp_path)
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "browsers/../cache")
	if init_cwd is None:
		monkeypatch.delenv("INIT_CWD", raising=False)
		base = tmp_path
	else:
		base = tmp_path / init_cwd
		monkeypatch.setenv("INIT_CWD", str(base))

	assert browser_runtime.patchright_browser_cache_dirs() == [base / "cache"]


def test_zero_selects_package_local_cache(monkeypatch: pytest.MonkeyPatch) -> None:
	import patchright

	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
	package_root = Path(patchright.__file__).resolve().parent / "driver" / "package"

	assert browser_runtime.patchright_browser_cache_dirs() == [package_root / ".local-browsers"]


def test_unknown_package_local_cache_does_not_reuse_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
	monkeypatch.setitem(sys.modules, "patchright", None)

	assert browser_runtime.patchright_browser_cache_dirs() == []


@pytest.mark.parametrize("custom_installed", [True, False])
def test_login_gate_uses_configured_cache_instead_of_healthy_default(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, custom_installed: bool,
) -> None:
	cache = tmp_path / "custom-cache"
	default_cache = tmp_path / ".cache" / "ms-playwright"
	for root in ([default_cache, cache] if custom_installed else [default_cache]):
		for name in ("chromium-123", "chromium_headless_shell-123"):
			entry = root / name
			entry.mkdir(parents=True)
			(entry / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
	monkeypatch.setattr(Path, "home", lambda: tmp_path)
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
	monkeypatch.setattr(browser_runtime, "patchright_chromium_revision", lambda: "123")

	status, _ = _browser_kernel_status()

	assert status == ("ok" if custom_installed else "error")
	assert all(entry.parent == cache for entry in browser_runtime.installed_browser_dirs())


@pytest.mark.parametrize("kind", ["absolute", "relative", "package"])
def test_selected_cache_matches_real_driver_without_launching_browser(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
	"""只启动本地驱动并读取路径；不启动浏览器、不下载、不请求平台。"""
	from patchright.sync_api import sync_playwright

	configured = {"absolute": str(tmp_path / "cache"), "relative": "cache", "package": "0"}[kind]
	monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", configured)
	monkeypatch.setenv("INIT_CWD", str(tmp_path))
	with sync_playwright() as driver:
		executable = Path(driver.chromium.executable_path)

	assert executable.is_relative_to(browser_runtime.patchright_browser_cache_dirs()[0])
