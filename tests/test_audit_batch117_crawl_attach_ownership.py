"""审计批次 117：附着的调试端口浏览器必须属于采集配置目录（Issue #2464）。

端口按 run 随机分配，但用户可以显式配置调试端口。端口上先有浏览器时
DrissionPage 会直接附着：那个浏览器可能是用户的日常 Chrome。没有归属校验
时，boss login 的 Cookie 会被注入用户自己的浏览器，保留窗口的关闭路径还会
退出它。这里固定三条语义：

1. 配置目录 SingletonLock 里的 Chrome 主进程 pid 与 CDP 报告的浏览器 pid
   一致才算本目录的采集浏览器；
2. 没有 SingletonLock（Windows）时读进程命令行的 ``--user-data-dir``；
3. 附着路径确认不了归属就按 ``CrawlProfileBusy`` 结算，且不注入 Cookie、
   不注册 Hook、不退出对方浏览器。空闲端口由本进程启动的浏览器保持既有行为。
"""

import socket
import sys
import types

import pytest

from boss_agent_cli.crawler import transport as transport_module
from boss_agent_cli.crawler.controls import CrawlProfileBusy
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.services.file_lock import exclusive_file_lock

PROFILE_PID = 4321
FOREIGN_PID = 9876


def _free_port() -> int:
	with socket.socket() as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


class _Listener:
	def start(self, *args, **kwargs) -> None:
		pass

	def stop(self) -> None:
		pass


class _Page:
	url = "https://www.zhipin.com/web/geek/jobs"
	html = "<div class='job-list-box'></div>"

	def __init__(self, process_id) -> None:
		self.browser = types.SimpleNamespace(process_id=process_id)
		self.listen = _Listener()
		self.injected = []
		self.quit_called = False
		self.set = types.SimpleNamespace(cookies=self._set_cookies)

	def _set_cookies(self, items) -> None:
		self.injected.append(items)

	def get(self, url: str) -> None:
		self.url = url

	def quit(self) -> None:
		self.quit_called = True


class _Options:
	def set_local_port(self, port: int) -> None:
		pass

	def set_user_data_path(self, path: str) -> None:
		pass

	def existing_only(self, on_off: bool = True) -> None:
		pass

	def set_browser_path(self, path: str) -> None:
		pass


@pytest.fixture
def fake_page(monkeypatch):
	state = {"page": None}

	def build(options):
		return state["page"]

	monkeypatch.setitem(
		sys.modules,
		"DrissionPage",
		types.SimpleNamespace(ChromiumOptions=_Options, ChromiumPage=build),
	)
	monkeypatch.setattr(transport_module, "inject_hook_profile", lambda page, profile, directory: [])
	return state


def _lock(profile, pid) -> None:
	profile.mkdir(parents=True, exist_ok=True)
	(profile / "SingletonLock").symlink_to(f"{socket.gethostname()}-{pid}")


def _session(profile, *, seed_cookies=None, port=None) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=profile,
		chrome_path=None,
		cdp_port=port if port is not None else _free_port(),
		hook_profile="none",
		hook_dir=None,
		seed_cookies=seed_cookies,
	)


def _set_attached(monkeypatch, attached: bool) -> None:
	monkeypatch.setattr(DrissionCrawlerSession, "_port_is_in_use", staticmethod(lambda port: attached))


# ── 附着路径：锁定归属才允许注入 ────────────────────────────────────


def test_attached_browser_owned_by_profile_lock_is_allowed(tmp_path, monkeypatch, fake_page):
	profile = tmp_path / "profile"
	_lock(profile, PROFILE_PID)
	page = _Page(PROFILE_PID)
	fake_page["page"] = page
	_set_attached(monkeypatch, True)

	results = _session(profile, seed_cookies={"wt2": "t"}).open()

	assert results == []
	assert len(page.injected) == 1, "归属确认后仍要注入登录 Cookie"
	assert page.quit_called is False


def test_attached_foreign_browser_is_refused_before_cookie_injection(tmp_path, monkeypatch, fake_page):
	profile = tmp_path / "profile"
	_lock(profile, PROFILE_PID)
	page = _Page(FOREIGN_PID)
	fake_page["page"] = page
	_set_attached(monkeypatch, True)
	monkeypatch.setattr(transport_module, "_browser_cmdline_profile", lambda pid: None)

	session = _session(profile, seed_cookies={"wt2": "t"})
	with pytest.raises(CrawlProfileBusy) as excinfo:
		session.open()

	assert "不是采集配置目录" in str(excinfo.value)
	assert page.injected == [], "未确认归属不能把 boss login 的 Cookie 注入外部浏览器"
	assert page.quit_called is False
	assert session._page is None, "边界错误不能把外部浏览器挂在会话上等 close() 去退出"


def test_attached_browser_with_foreign_user_data_dir_is_refused(tmp_path, monkeypatch, fake_page):
	profile = tmp_path / "profile"
	_lock(profile, PROFILE_PID)
	page = _Page(FOREIGN_PID)
	fake_page["page"] = page
	_set_attached(monkeypatch, True)
	monkeypatch.setattr(transport_module, "_browser_cmdline_profile", lambda pid: str(tmp_path / "default-profile"))

	with pytest.raises(CrawlProfileBusy):
		_session(profile).open()

	assert page.injected == []


def test_attached_browser_without_readable_process_id_is_refused(tmp_path, monkeypatch, fake_page):
	profile = tmp_path / "profile"
	_lock(profile, PROFILE_PID)
	fake_page["page"] = _Page(None)
	_set_attached(monkeypatch, True)

	with pytest.raises(CrawlProfileBusy) as excinfo:
		_session(profile).open()

	assert "进程号不可读" in str(excinfo.value)


def test_attached_browser_matching_user_data_dir_cmdline_is_allowed(tmp_path, monkeypatch, fake_page):
	# Windows 没有 SingletonLock：命令行里的 --user-data-dir 是唯一归属证据。
	profile = tmp_path / "profile"
	profile.mkdir()
	page = _Page(FOREIGN_PID)
	fake_page["page"] = page
	_set_attached(monkeypatch, True)
	monkeypatch.setattr(transport_module, "_browser_cmdline_profile", lambda pid: str(profile))

	_session(profile, seed_cookies={"wt2": "t"}).open()

	assert len(page.injected) == 1


# ── 启动路径：竞态附着同样要拒绝 ───────────────────────────────────


def test_free_port_launch_rejects_browser_from_another_profile(tmp_path, monkeypatch, fake_page):
	# 端口探测与 ChromiumPage 构造之间端口被别人抢走时，DrissionPage 会附着
	# 而不是启动；命令行归属不一致必须拒绝。
	profile = tmp_path / "profile"
	page = _Page(FOREIGN_PID)
	fake_page["page"] = page
	_set_attached(monkeypatch, False)
	monkeypatch.setattr(transport_module, "_browser_cmdline_profile", lambda pid: str(tmp_path / "other-profile"))

	with pytest.raises(CrawlProfileBusy) as excinfo:
		_session(profile).open()

	assert "不是采集配置目录" in str(excinfo.value)
	assert page.injected == []


def test_free_port_launch_with_unverifiable_process_stays_allowed(tmp_path, monkeypatch, fake_page):
	# 端口空闲时浏览器由本进程拉起；进程号读不到（测试替身、受限平台）保持
	# 既有启动行为，不能把正常启动误判成外部浏览器。
	profile = tmp_path / "profile"
	page = _Page(None)
	fake_page["page"] = page
	_set_attached(monkeypatch, False)

	_session(profile, seed_cookies={"wt2": "t"}).open()

	assert len(page.injected) == 1


# ── 配置目录打开锁 ─────────────────────────────────────────────────


def test_open_refuses_profile_being_opened_by_another_process(tmp_path, monkeypatch, fake_page):
	profile = tmp_path / "profile"
	page = _Page(None)
	fake_page["page"] = page
	_set_attached(monkeypatch, False)
	lock_path = profile / ".crawl-open.lock"
	with exclusive_file_lock(lock_path) as acquired:
		assert acquired
		with pytest.raises(CrawlProfileBusy) as excinfo:
			_session(profile).open()

	assert "正被另一个进程打开" in str(excinfo.value)
	assert page.injected == []
	assert not lock_path.exists(), "释放后不留下孤儿锁文件"


# ── 命令行回退的解析 ───────────────────────────────────────────────


def _fake_psutil(cmdline, *, error=None):
	class _Process:
		def __init__(self, pid: int) -> None:
			self.pid = pid

		def cmdline(self):
			if error is not None:
				raise error
			return cmdline

	class _Error(Exception):
		pass

	return types.SimpleNamespace(Process=_Process, Error=_Error)


def test_browser_cmdline_profile_extracts_user_data_dir(monkeypatch):
	fake = _fake_psutil(["chrome", "--user-data-dir=/tmp/crawl-profile", "--remote-debugging-port=9333"])
	monkeypatch.setitem(sys.modules, "psutil", fake)

	assert transport_module._browser_cmdline_profile(123) == "/tmp/crawl-profile"


def test_browser_cmdline_profile_returns_none_without_flag(monkeypatch):
	monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(["chrome", "--remote-debugging-port=9333"]))

	assert transport_module._browser_cmdline_profile(123) is None


def test_browser_cmdline_profile_returns_none_when_psutil_is_missing(monkeypatch):
	# crawl 依赖缺失时导入会失败；归属校验按「无法确认」交给调用方决定。
	monkeypatch.setitem(sys.modules, "psutil", None)

	assert transport_module._browser_cmdline_profile(123) is None


def test_browser_cmdline_profile_returns_none_when_process_is_unreadable(monkeypatch):
	fake = _fake_psutil([], error=PermissionError("denied"))
	monkeypatch.setitem(sys.modules, "psutil", fake)

	assert transport_module._browser_cmdline_profile(123) is None


def test_same_profile_directory_resolves_relative_paths(tmp_path, monkeypatch):
	profile = tmp_path / "profile"
	profile.mkdir()
	monkeypatch.chdir(tmp_path)

	assert transport_module._same_profile_directory("profile", profile) is True
	assert transport_module._same_profile_directory(str(profile), profile) is True
	assert transport_module._same_profile_directory(str(profile / "nested"), profile) is False
