"""CDP 模式请求安全（code 37 现场复盘后的修复）。

现场：CDP 模式下 job_card 先走 httpx（带着从 Chrome 拷来的 Cookie 和 __zp_stoken__ 查询参数），
与浏览器通道并发打平台，结果 Chrome 里的 stoken 被判异常，之后 CLI 的浏览器请求一律 code 37。
这里全部离线：浏览器会话、httpx、CDP cookie 读取都是替身，不连任何真实浏览器或平台。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api import cdp_risk_lock, endpoints
from boss_agent_cli.api._base_client import BrowserChannelRequired
from boss_agent_cli.api.browser_client import BrowserSession
from boss_agent_cli.api.client import BossClient, EnvironmentRiskError, EnvironmentRiskLockedError, PlatformRiskError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin import BossPlatform

RAW_STOKEN = "raw-zp-stoken-value-should-never-hit-disk"
NEW_STOKEN = "fresh-zp-stoken-after-page-security-check"
ENV_RISK = {"code": 37, "message": "您的访问环境存在异常，请稍后再试"}


class FakeAuth:
	def __init__(self, data_dir: Path) -> None:
		self.data_dir = data_dir
		self.refresh_calls: list[Any] = []

	def get_token(self) -> dict[str, Any]:
		return {"cookies": {"wt2": "w", "__zp_stoken__": RAW_STOKEN}, "stoken": RAW_STOKEN, "user_agent": "ua"}

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None) -> None:
		self.refresh_calls.append((cdp_url, browser_source))


class FakeContext:
	def __init__(self, stoken: str | None) -> None:
		self.stoken = stoken
		self.fail = False

	def cookies(self, *urls: Any) -> list[dict[str, Any]]:
		if self.fail:
			raise RuntimeError("cdp gone")
		cookies = [{"name": "wt2", "value": "w", "domain": ".zhipin.com"}]
		if self.stoken is not None:
			cookies.append({"name": "__zp_stoken__", "value": self.stoken, "domain": ".zhipin.com"})
		return cookies


class FakeBrowser:
	"""浏览器会话替身：复用真实的 current_stoken_hash，request 只记录不触网。"""

	current_stoken_hash = BrowserSession.current_stoken_hash

	def __init__(self, *, is_cdp: bool = True, stoken: str | None = RAW_STOKEN, responses: list[dict] | None = None) -> None:
		self._is_cdp = is_cdp
		self._context = FakeContext(stoken)
		self.responses = list(responses or [])
		self.calls: list[tuple[str, str, Any, Any]] = []
		self.started = 0

	def ensure_started(self) -> None:
		self.started += 1

	def request(self, method: str, url: str, *, params: Any = None, data: Any = None) -> dict[str, Any]:
		self.calls.append((method, url, params, data))
		return self.responses.pop(0) if self.responses else {"code": 0, "zpData": {}}

	def close(self) -> None:
		pass


def _client(tmp_path: Path, browser: FakeBrowser, *, cdp_url: str | None = "http://127.0.0.1:9222", source: str | None = None) -> BossClient:
	client = BossClient(FakeAuth(tmp_path), cdp_url=cdp_url, browser_source=source)
	client._browser_session = browser  # type: ignore[assignment]
	return client


@pytest.fixture
def no_httpx():
	with patch("boss_agent_cli.api._base_client.httpx.Client") as mock_cls:
		yield mock_cls


# ── 1. CDP 模式下平台请求一律不走 httpx ─────────────────────────────


def test_cdp_job_card_goes_browser_only(tmp_path, no_httpx):
	browser = FakeBrowser(responses=[{"code": 0, "zpData": {"jobCard": {}}}])
	client = _client(tmp_path, browser)
	with patch.object(client, "job_card_httpx") as mock_httpx:
		client.job_card("sec", "lid")
	mock_httpx.assert_not_called()
	no_httpx.assert_not_called()
	assert browser.calls == [("GET", endpoints.JOB_CARD_URL, {"securityId": "sec", "lid": "lid"}, None)]


def test_cdp_job_card_browser_risk_is_not_retried(tmp_path, no_httpx):
	browser = FakeBrowser(responses=[ENV_RISK])
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskError):
		client.job_card("sec")
	assert len(browser.calls) == 1
	no_httpx.assert_not_called()


@pytest.mark.parametrize(
	"call,url",
	[
		(lambda c: c.job_detail("jid"), endpoints.DETAIL_URL),
		(lambda c: c.user_info(), endpoints.USER_INFO_URL),
		(lambda c: c.resume_baseinfo(), endpoints.RESUME_BASEINFO_URL),
		(lambda c: c.resume_expect(), endpoints.RESUME_EXPECT_URL),
		(lambda c: c.deliver_list(2), endpoints.DELIVER_LIST_URL),
		(lambda c: c.job_favorites(), endpoints.GEEK_GET_JOB_URL),
		(lambda c: c.friend_list(), endpoints.FRIEND_LIST_URL),
		(lambda c: c.interview_data(), endpoints.INTERVIEW_DATA_URL),
		(lambda c: c.job_history(), endpoints.JOB_HISTORY_URL),
		(lambda c: c.chat_history("g", "s"), endpoints.CHAT_HISTORY_URL),
		(lambda c: c.friend_label("f", 1), endpoints.FRIEND_LABEL_ADD_URL),
		(lambda c: c.resume_status(), endpoints.RESUME_STATUS_URL),
		(lambda c: c.geek_get_job("s"), endpoints.GEEK_GET_JOB_URL),
	],
)
@pytest.mark.parametrize("cdp_url,source", [("http://127.0.0.1:9222", None), (None, "existing-browser"), (None, "stored-cookie")])
def test_cdp_httpx_methods_are_routed_to_browser(tmp_path, no_httpx, call, url, cdp_url, source):
	browser = FakeBrowser()
	client = _client(tmp_path, browser, cdp_url=cdp_url, source=source)
	call(client)
	no_httpx.assert_not_called()
	assert client._client is None
	assert [c[1] for c in browser.calls] == [url]
	# 浏览器通道的参数里不会被塞进 httpx 风格的 __zp_stoken__
	params = browser.calls[0][2] or {}
	assert "__zp_stoken__" not in params
	assert client._auth.refresh_calls == []


def test_cdp_get_client_is_blocked(tmp_path, no_httpx):
	client = _client(tmp_path, FakeBrowser())
	with pytest.raises(BrowserChannelRequired):
		client._get_client()
	no_httpx.assert_not_called()


def test_auto_mode_switches_to_browser_after_session_attached_to_cdp(tmp_path, no_httpx):
	"""auto 模式自动探测接上 CDP Chrome 后，同进程后续请求也不再走 httpx。"""
	browser = FakeBrowser(is_cdp=True)
	client = _client(tmp_path, browser, cdp_url=None)
	assert client.is_browser_only() is True
	client.job_detail("jid")
	no_httpx.assert_not_called()


def test_non_cdp_job_card_keeps_httpx_first(tmp_path):
	client = BossClient(FakeAuth(tmp_path))
	assert client.is_browser_only() is False
	with (
		patch.object(client, "job_card_httpx", return_value={"code": 0}) as mock_httpx,
		patch.object(client, "_browser_request") as mock_browser,
	):
		client.job_card("sec", "lid")
	mock_httpx.assert_called_once_with("sec", "lid")
	mock_browser.assert_not_called()


def test_non_cdp_low_risk_reads_stay_on_httpx(tmp_path):
	client = BossClient(FakeAuth(tmp_path))
	http_client = MagicMock()
	response = MagicMock(status_code=200, text="{}", cookies={})
	response.json.return_value = {"code": 0}
	http_client.request.return_value = response
	client._client = http_client
	client._throttle = MagicMock()
	with patch.object(client, "_browser_request") as mock_browser:
		client.job_detail("jid")
	mock_browser.assert_not_called()
	assert http_client.request.call_args.kwargs["params"]["__zp_stoken__"] == RAW_STOKEN


def test_headless_session_does_not_make_auto_mode_browser_only(tmp_path):
	client = _client(tmp_path, FakeBrowser(is_cdp=False), cdp_url=None)
	assert client.is_browser_only() is False


def test_recruiter_cdp_reads_go_browser_and_download_is_refused(tmp_path, no_httpx):
	client = BossRecruiterClient(FakeAuth(tmp_path), cdp_url="http://127.0.0.1:9222")
	browser = FakeBrowser()
	client._browser_session = browser  # type: ignore[assignment]
	client.friend_labels()
	assert [c[1] for c in browser.calls] and no_httpx.call_count == 0
	with pytest.raises(BrowserChannelRequired):
		client._get_client()


# ── 2. CDP 模式下详情不并发 ──────────────────────────────────────────


def test_pipeline_cdp_details_are_sequential_and_browser_only(tmp_path):
	from boss_agent_cli.output import Logger
	from boss_agent_cli.search_filters import SearchFilterCriteria, run_search_pipeline

	order: list[str] = []
	client = MagicMock()
	client.name = "zhipin"
	client.is_browser_only.return_value = True
	client.is_success.side_effect = lambda r: r.get("code") == 0
	client.unwrap_data.side_effect = lambda r: r.get("zpData")
	jobs = [
		{"encryptJobId": f"j{i}", "securityId": f"s{i}", "jobName": "Python", "brandName": "C", "welfareList": []}
		for i in range(3)
	]
	client.search_jobs.return_value = {"code": 0, "zpData": {"jobList": jobs, "hasMore": False}}

	def card(sid: str, lid: str = "") -> dict[str, Any]:
		order.append(f"card:{sid}")
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "双休"}}}

	client.job_card_browser.side_effect = card
	cache = MagicMock()
	cache.get_job_desc.return_value = None
	cache.is_greeted.return_value = False
	with patch("boss_agent_cli.search_filters.ThreadPoolExecutor", side_effect=AssertionError("CDP 下不得并发")):
		result = run_search_pipeline(
			client, cache, Logger(),
			criteria=SearchFilterCriteria(query="Python"),
			welfare_conditions=[("双休", ["双休"])],
			before_detail_request=lambda: order.append("wait"),
		)
	client.job_card.assert_not_called()
	assert order == ["wait", "card:s0", "wait", "card:s1", "wait", "card:s2"]
	assert result.total == 3


def test_execute_candidate_search_adds_budget_for_browser_only_platform(tmp_path):
	from boss_agent_cli.cache.store import CacheStore
	from boss_agent_cli.output import Logger
	from boss_agent_cli.wizard.actions import execute_candidate_search

	captured: dict[str, Any] = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return MagicMock()

	platform = BossPlatform(MagicMock())
	platform._client.is_browser_only.return_value = True
	with CacheStore(tmp_path / "cache.db") as cache:
		execute_candidate_search(platform, cache, Logger(), {"query": "go", "welfare": "双休"}, pipeline=pipeline)
	assert captured["detail_channel"] == "browser"
	assert callable(captured["before_detail_request"])


def test_execute_candidate_search_non_cdp_unchanged(tmp_path):
	from boss_agent_cli.output import Logger
	from boss_agent_cli.wizard.actions import execute_candidate_search

	captured: dict[str, Any] = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return MagicMock()

	platform = BossPlatform(BossClient(FakeAuth(tmp_path)))
	execute_candidate_search(platform, None, Logger(), {"query": "go", "welfare": "双休"}, pipeline=pipeline)  # type: ignore[arg-type]
	assert "detail_channel" not in captured
	assert "before_detail_request" not in captured


# ── 3. code 37 锁 ────────────────────────────────────────────────────


def test_code37_creates_lock_with_hash_only(tmp_path, no_httpx):
	browser = FakeBrowser(responses=[ENV_RISK])
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskError) as exc_info:
		client.job_card_browser("sec")
	assert not isinstance(exc_info.value, EnvironmentRiskLockedError)

	path = tmp_path / cdp_risk_lock.LOCK_FILENAME
	text = path.read_text(encoding="utf-8")
	assert RAW_STOKEN not in text
	payload = json.loads(text)
	assert payload["stoken_sha256"] == hashlib.sha256(RAW_STOKEN.encode()).hexdigest()
	assert payload["locked_at"] > 0


def test_locked_same_stoken_refuses_without_sending(tmp_path, no_httpx):
	browser = FakeBrowser(responses=[ENV_RISK])
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskError):
		client.search_jobs("Python")
	assert len(browser.calls) == 1

	# 新进程 / 新 client 同样受锁约束
	browser2 = FakeBrowser(stoken=RAW_STOKEN)
	client2 = _client(tmp_path, browser2)
	with pytest.raises(EnvironmentRiskLockedError) as exc_info:
		client2.job_detail("jid")
	assert browser2.calls == []
	assert exc_info.value.code == "ENVIRONMENT_RISK_LOCKED"
	assert isinstance(exc_info.value, PlatformRiskError)
	assert "职位列表页" in str(exc_info.value)
	assert RAW_STOKEN not in str(exc_info.value)
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_locked_changed_stoken_proceeds_and_clears(tmp_path, no_httpx):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	browser = FakeBrowser(stoken=NEW_STOKEN)
	client = _client(tmp_path, browser)
	assert client.job_card("sec")["code"] == 0
	assert len(browser.calls) == 1
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_lock_unreadable_stoken_fails_closed(tmp_path, no_httpx):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	browser = FakeBrowser(stoken=NEW_STOKEN)
	browser._context.fail = True
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskLockedError):
		client.job_card("sec")
	assert browser.calls == []


def test_corrupt_lock_fails_closed(tmp_path, no_httpx):
	(tmp_path / cdp_risk_lock.LOCK_FILENAME).write_text("{not json", encoding="utf-8")
	browser = FakeBrowser(stoken=NEW_STOKEN)
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskLockedError):
		client.user_info()
	assert browser.calls == []


def test_lock_does_not_apply_to_headless_session(tmp_path):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	browser = FakeBrowser(is_cdp=False)
	client = _client(tmp_path, browser, cdp_url=None)
	client.search_jobs("Python")
	assert len(browser.calls) == 1
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_absent_stoken_lock_unlocks_when_stoken_appears(tmp_path, no_httpx):
	browser = FakeBrowser(stoken=None, responses=[ENV_RISK])
	client = _client(tmp_path, browser)
	with pytest.raises(EnvironmentRiskError):
		client.search_jobs("Python")
	assert json.loads((tmp_path / cdp_risk_lock.LOCK_FILENAME).read_text())["stoken_sha256"] == cdp_risk_lock.ABSENT_STOKEN

	same = FakeBrowser(stoken=None)
	with pytest.raises(EnvironmentRiskLockedError):
		_client(tmp_path, same).search_jobs("Python")
	fresh = FakeBrowser(stoken=NEW_STOKEN)
	_client(tmp_path, fresh).search_jobs("Python")
	assert len(fresh.calls) == 1


def test_token_expired_code37_does_not_lock(tmp_path, no_httpx):
	browser = FakeBrowser(responses=[{"code": 37, "message": "__zp_stoken__ 已过期"}])
	_client(tmp_path, browser).search_jobs("Python")
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_recruiter_code37_records_and_respects_lock(tmp_path, no_httpx):
	client = BossRecruiterClient(FakeAuth(tmp_path), cdp_url="http://127.0.0.1:9222")
	browser = FakeBrowser(responses=[ENV_RISK])
	client._browser_session = browser  # type: ignore[assignment]
	result = client.friend_labels()
	assert result["code"] == 37
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()
	with pytest.raises(EnvironmentRiskLockedError):
		client.friend_labels()
	assert len(browser.calls) == 1


def test_browser_session_stoken_hash_reads_context_cookies_only():
	session = BrowserSession(cookies={}, user_agent="")
	assert session.current_stoken_hash() is None  # 未连 CDP
	session._is_cdp = True
	session._context = FakeContext(RAW_STOKEN)
	assert session.current_stoken_hash() == hashlib.sha256(RAW_STOKEN.encode()).hexdigest()
	session._context = FakeContext(None)
	assert session.current_stoken_hash() == cdp_risk_lock.ABSENT_STOKEN
	session._is_cdp = False


@pytest.mark.parametrize("domain", [".example.com", "evilzhipin.com", "zhipin.com.evil.io"])
def test_hash_ignores_foreign_domain_cookie(domain):
	cookies = [{"name": "__zp_stoken__", "value": "x", "domain": domain}]
	assert cdp_risk_lock.stoken_hash_from_cookies(cookies) == cdp_risk_lock.ABSENT_STOKEN


@pytest.mark.parametrize("domain", [".zhipin.com", "zhipin.com", "www.zhipin.com"])
def test_hash_reads_zhipin_cookie(domain):
	cookies = [{"name": "__zp_stoken__", "value": "x", "domain": domain}]
	assert cdp_risk_lock.stoken_hash_from_cookies(cookies) == cdp_risk_lock.hash_stoken("x")


def test_locked_error_envelope(tmp_path):
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	spec = ERROR_CODES["ENVIRONMENT_RISK_LOCKED"]
	assert spec["recoverable"] is False
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	with patch("boss_agent_cli.commands.detail.get_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.job_detail.side_effect = EnvironmentRiskLockedError.from_lock(cdp_risk_lock.read_lock(tmp_path))
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "detail", "sec", "--job-id", "jid"])
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "ENVIRONMENT_RISK_LOCKED"
	assert payload["error"]["recoverable"] is False
	# 风控命中即终止：不再换 job_card 补请求
	platform.job_card.assert_not_called()


# ── 4. doctor / status / clean ──────────────────────────────────────


def _doctor(tmp_path: Path) -> dict[str, Any]:
	with (
		patch("boss_agent_cli.commands.doctor.AuthManager") as mock_auth,
		patch("boss_agent_cli.commands.doctor.probe_cdp", return_value=None),
		patch("boss_agent_cli.commands.doctor.httpx.get", side_effect=RuntimeError("offline")),
		patch("boss_agent_cli.commands.doctor.extract_cookies", return_value=None),
	):
		mock_auth.return_value.check_status.return_value = None
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "doctor"])
	return json.loads(result.output)


def test_doctor_reports_lock_without_network(tmp_path):
	parsed = _doctor(tmp_path)
	check = next(c for c in parsed["data"]["checks"] if c["name"] == "cdp_risk_lock")
	assert check["status"] == "ok"
	assert parsed["data"]["cdp_risk_lock"] == {"locked": False}

	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	parsed = _doctor(tmp_path)
	check = next(c for c in parsed["data"]["checks"] if c["name"] == "cdp_risk_lock")
	assert check["status"] == "warn"
	assert "boss clean --risk-lock" in check["hint"]
	assert parsed["data"]["cdp_risk_lock"]["locked"] is True
	assert RAW_STOKEN not in json.dumps(parsed, ensure_ascii=False)
	assert any("职位列表页" in step for step in parsed["hints"]["operator_actions"])


def test_status_reports_lock_offline(tmp_path):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	token = {"cookies": {"wt2": "w", "wbg": "1", "zp_at": "a"}, "stoken": "s", "user_agent": "ua"}
	with (
		patch("boss_agent_cli.commands.status.AuthManager") as mock_auth,
		patch("boss_agent_cli.commands.status.get_platform_instance") as factory,
	):
		mock_auth.return_value.check_status.return_value = token
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "status"])
	factory.assert_not_called()
	payload = json.loads(result.output)
	assert payload["data"]["cdp_risk_lock"]["locked"] is True
	assert payload["data"]["cdp_risk_lock"]["stoken_recorded"] is True


def test_clean_risk_lock_removes_lock(tmp_path):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "clean", "--risk-lock", "--dry-run"])
	assert result.exit_code == 0, result.output
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "clean", "--risk-lock"])
	assert result.exit_code == 0, result.output
	rows = json.loads(result.output)["data"]["results"]
	assert {"target": "CDP 风控锁", "cleaned": 1} == {k: rows[0][k] for k in ("target", "cleaned")}
	assert not (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()


def test_clean_without_flag_keeps_lock(tmp_path):
	cdp_risk_lock.write_lock(tmp_path, cdp_risk_lock.hash_stoken(RAW_STOKEN))
	CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "clean"])
	assert (tmp_path / cdp_risk_lock.LOCK_FILENAME).exists()
