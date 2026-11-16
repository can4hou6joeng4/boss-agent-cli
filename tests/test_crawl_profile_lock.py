"""采集配置目录的并发保护（Issue #1266）。

同一数据目录下的不同 crawl run 共用 `<data_dir>/crawl/chrome-profile`，但端口按
run 随机分配。端口空闲不代表配置目录空闲：另一个 run 的采集 Chrome 可能正用它。
`CrawlTransport.open()` 原先在端口空闲时无条件删除 SingletonLock 等文件，会
破坏正在运行的采集；现在先读锁里的 `<hostname>-<pid>`，进程还活着就报
`CrawlProfileBusy`（不删锁、不启动），只有确实陈旧的锁才清理。
"""

import json
import os
import socket
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import crawl as crawl_module
from boss_agent_cli.crawler.controls import CrawlProfileBusy
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.main import cli


def _free_port() -> int:
	with socket.socket() as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _session(profile: Path) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=profile,
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		hook_dir=None,
	)


@pytest.fixture
def fake_drission(monkeypatch):
	class Options:
		def set_browser_path(self, path: str) -> None:
			pass

		def set_local_port(self, port: int) -> None:
			pass

		def set_user_data_path(self, path: str) -> None:
			pass

		def existing_only(self, on_off: bool = True) -> None:
			pass

	class Page:
		url = ""
		html = ""

		def quit(self) -> None:
			pass

	monkeypatch.setitem(sys.modules, "DrissionPage", types.SimpleNamespace(
		ChromiumOptions=Options,
		ChromiumPage=lambda options: Page(),
	))
	monkeypatch.setattr(
		"boss_agent_cli.crawler.transport.inject_hook_profile",
		lambda page, profile, directory: [],
	)


def _lock(profile: Path, target: str) -> Path:
	profile.mkdir(parents=True, exist_ok=True)
	path = profile / "SingletonLock"
	path.symlink_to(target)
	return path


def test_open_refuses_profile_held_by_live_chrome(tmp_path, fake_drission):
	profile = tmp_path / "profile"
	lock = _lock(profile, f"{socket.gethostname()}-{os.getpid()}")

	with pytest.raises(CrawlProfileBusy) as excinfo:
		_session(profile).open()

	# 报出真正占用配置目录的进程，并保留它的锁文件。
	assert str(os.getpid()) in str(excinfo.value)
	assert lock.is_symlink()


def test_open_clears_lock_of_dead_process(tmp_path, fake_drission):
	profile = tmp_path / "profile"
	# pid 上限远小于这个值：锁指向的进程一定不存在，属于异常退出遗留。
	lock = _lock(profile, f"{socket.gethostname()}-99999999")

	_session(profile).open()

	assert not lock.is_symlink()
	assert not lock.exists()


def test_open_clears_lock_from_another_host(tmp_path, fake_drission):
	profile = tmp_path / "profile"
	# 别的机器上的 pid 不能证明本机配置目录被占用：按陈旧锁处理。
	lock = _lock(profile, f"some-other-host-{os.getpid()}")

	_session(profile).open()

	assert not lock.is_symlink()


def test_open_clears_unparsable_lock(tmp_path, fake_drission):
	profile = tmp_path / "profile"
	lock = _lock(profile, "not-a-chrome-lock")

	_session(profile).open()

	assert not lock.is_symlink()


def test_crawl_run_reports_profile_busy_envelope(tmp_path, monkeypatch):
	# 命令层只负责把 transport 的并发错误映射成信封：真实浏览器在这里不参与。
	monkeypatch.setattr(crawl_module, "_seeded_transport_factory", lambda ctx: (lambda settings: None))

	def busy(self, settings):
		raise CrawlProfileBusy("采集配置目录正被运行中的采集 Chrome（pid 123）使用")

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", busy)
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "crawl", "run", "AI", "--city", "杭州"]
	)

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	error = payload["error"]
	assert error["code"] == "CRAWL_PROFILE_BUSY"
	assert error["recoverable"] is True
	assert "正被运行中的采集 Chrome" in error["message"]
	assert "SingletonLock" in error["recovery_action"]
	assert any("crawl status" in action for action in payload["hints"]["next_actions"])
