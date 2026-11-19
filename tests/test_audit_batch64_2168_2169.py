"""采集 Cookie 值级脱敏与 crawl 路径解析的符号链接环收口（Issue #2168 / #2169）。

DrissionPage 的注入异常会把整个 cookie 字典（含 ``value``）拼进消息，
而 ``_failure_reason`` 原先只截断不脱敏，最终进入 ``AUTH_REQUIRED`` 信封。
``Path.resolve()`` 在 3.11 / 3.12 遇到符号链接环抛 ``RuntimeError``，3.13+ 改为
返回原路径；这里按路径 monkeypatch 注入 ``RuntimeError``，断言与解释器版本无关。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlProfilePathInvalid, CrawlSessionAuthError
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.main import cli

SECRET = "SECRET-COOKIE-VALUE-123456"


def _loop_resolve_for(monkeypatch: pytest.MonkeyPatch, predicate) -> None:
	"""只让满足 predicate 的路径 resolve() 抛 RuntimeError，其余保持原行为。"""
	original = Path.resolve

	def fake(self: Path, *args, **kwargs):
		if predicate(self):
			raise RuntimeError(f"Symlink loop from '{self}'")
		return original(self, *args, **kwargs)

	monkeypatch.setattr(Path, "resolve", fake)


class _LeakingCookieSet:
	"""模拟 DrissionPage：异常文本原样携带整个 cookie 字典。"""

	def cookies(self, payload) -> None:
		raise ValueError(f"No domain name is set for cookie, cookie: {payload}")


class _SeedFailingPage:
	url = "https://www.zhipin.com/web/geek/job"
	html = "<html><body>请登录</body></html>"
	set = _LeakingCookieSet()

	def get(self, url: str) -> None:
		raise RuntimeError("navigation failed")


def _session(tmp_path: Path, *, seed_cookies: dict[str, str] | None = None) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		hook_dir=None,
		seed_cookies=seed_cookies if seed_cookies is not None else {"wt2": SECRET},
	)


# ── Issue #2168：失败原因的 Cookie 值脱敏 ──────────────────────────


def test_failure_reason_redacts_secret_values() -> None:
	error = ValueError(f"No domain name is set for cookie, cookie: {{'name': 'wt2', 'value': '{SECRET}'}}")

	text = DrissionCrawlerSession._failure_reason(error, secrets=[SECRET, ""])

	assert SECRET not in text
	assert "[REDACTED]" in text
	assert text.startswith("ValueError: ")


def test_failure_reason_without_secrets_keeps_type_and_text() -> None:
	text = DrissionCrawlerSession._failure_reason(RuntimeError("navigation failed"))

	assert text == "RuntimeError: navigation failed"


def test_seed_cookie_failure_does_not_leak_values_into_auth_error(tmp_path: Path) -> None:
	"""注入失败 → CrawlSessionAuthError 的文本不得包含真实 Cookie 值。"""
	session = _session(tmp_path)
	session._page = _SeedFailingPage()

	seed = session._seed_session_cookies()

	assert seed.failures and SECRET not in seed.failures[0]
	assert "[REDACTED]" in seed.failures[0]
	with pytest.raises(CrawlSessionAuthError) as excinfo:
		session._raise_if_seed_auth_failure(seed)
	message = str(excinfo.value)
	assert SECRET not in message
	assert "[REDACTED]" in message
	assert "ValueError" in message


def test_seed_navigation_failure_does_not_leak_values(tmp_path: Path) -> None:
	"""seed 首页导航异常同样走值级脱敏，避免第三方文本携带原值。"""

	class _LeakingNavigationPage(_SeedFailingPage):
		def get(self, url: str) -> None:
			raise RuntimeError(f"proxy rejected header cookie={SECRET}")

	session = _session(tmp_path)
	session._page = _LeakingNavigationPage()

	seed = session._seed_session_cookies()

	assert SECRET not in seed.navigation_error
	assert "[REDACTED]" in seed.navigation_error


# ── Issue #2169：符号链接环的 RuntimeError 收口 ────────────────────


def test_transport_open_maps_resolve_loop_to_profile_path_invalid(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	profile = tmp_path / "crawl" / "chrome-profile"
	profile.parent.mkdir(parents=True)
	_loop_resolve_for(monkeypatch, lambda path: str(path) == str(profile))
	monkeypatch.setitem(
		sys.modules,
		"DrissionPage",
		types.SimpleNamespace(ChromiumOptions=lambda: object(), ChromiumPage=lambda options: object()),
	)
	session = _session(tmp_path)

	with pytest.raises(CrawlProfilePathInvalid) as excinfo:
		session.open()

	assert "符号链接环" in str(excinfo.value)


def test_crawl_start_resolve_loop_reports_invalid_param(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""数据目录下的配置路径无法解析：参数错误，不是可重试的 NETWORK_ERROR。"""
	profile = tmp_path / "crawl" / "chrome-profile"
	_loop_resolve_for(monkeypatch, lambda path: str(path) == str(profile))

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "start", "AI", "--city", "杭州"])

	assert result.exit_code == 1, result.stdout
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	error = envelope["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "符号链接环" in error["message"]


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		operating_mode="research",
	)


def test_service_create_maps_resolve_loop_to_profile_path_invalid(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""输出目录无法解析：确定性本地路径失败，不是可重试网络错误。"""
	runs_dir = tmp_path / "crawl" / "runs"
	_loop_resolve_for(monkeypatch, lambda path: path.parent == runs_dir)
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: object(),
		budget_factory=_NoDelayBudget,
	)

	with cache, pytest.raises(CrawlProfilePathInvalid) as excinfo:
		service.create(_settings(tmp_path))

	assert "输出目录" in str(excinfo.value)
	assert "符号链接环" in str(excinfo.value)


def test_crawl_start_reports_local_io_error_when_output_dir_unresolvable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""start 的输出目录解析失败按 LOCAL_IO_ERROR 收口，不再兜成 NETWORK_ERROR。"""
	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent"}
	)
	runs_dir = tmp_path / "crawl" / "runs"
	_loop_resolve_for(monkeypatch, lambda path: path.parent == runs_dir)
	runner = CliRunner()
	runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])

	result = runner.invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "start", "AI", "--city", "杭州"])

	assert result.exit_code == 1, result.stdout
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	error = envelope["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert "符号链接环" in error["message"]
