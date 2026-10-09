"""#451 回归：--active 的请求量控制（online 只看列表、其余档位串行取详情、翻页有上限）。

全部离线：FakeClient / mock，不访问任何真实平台或浏览器。
"""
import json
import threading
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.models import JobItem, is_boss_online
from boss_agent_cli.main import cli
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchPipelinePlatformError,
	SearchPipelineResult,
	SearchPipelineStats,
	detail_channel_for,
	detail_filter_max_pages,
	list_active_decision,
	run_search_pipeline,
)


class FakeLogger:
	def __init__(self):
		self.messages: list[str] = []

	def info(self, message: str):
		self.messages.append(message)


class FakeCache:
	def is_greeted(self, security_id: str) -> bool:
		return False

	def get_job_desc(self, job_id: str):
		return None

	def put_job_desc(self, job_id: str, description: str) -> None:
		pass


class FakeClient:
	name = "zhipin"

	def __init__(self, pages: list[dict], cards: dict | None = None, events: list | None = None):
		self.pages = list(pages)
		self.cards = cards or {}
		self.list_calls = 0
		self.detail_calls: list[str] = []
		self.events = events if events is not None else []
		self._in_flight = 0
		self.max_in_flight = 0
		self._lock = threading.Lock()

	def search_jobs(self, query: str, **filters):
		self.list_calls += 1
		return self.pages.pop(0)

	def is_success(self, response: dict) -> bool:
		return response.get("code", 0) == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		if response.get("code") == 37:
			return "ENVIRONMENT_RISK", "环境异常"
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict):
		return response.get("zpData")

	def job_card(self, security_id: str, lid: str = ""):
		with self._lock:
			self._in_flight += 1
			self.max_in_flight = max(self.max_in_flight, self._in_flight)
		try:
			self.detail_calls.append(security_id)
			self.events.append(f"card:{security_id}")
			card = self.cards[security_id]
			if isinstance(card, Exception):
				raise card
			if isinstance(card, dict) and "code" in card:
				return card
			return {"code": 0, "zpData": {"jobCard": card}}
		finally:
			with self._lock:
				self._in_flight -= 1


class BrowserFakeClient(FakeClient):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.browser_calls: list[str] = []

	def job_card(self, security_id: str, lid: str = ""):  # httpx 优先通道，CDP 模式下不应被调用
		raise AssertionError("CDP 模式不应走 job_card（httpx 优先）")

	def job_card_browser(self, security_id: str, lid: str = ""):
		self.browser_calls.append(security_id)
		return {"code": 0, "zpData": {"jobCard": self.cards[security_id]}}


def _job(sid: str, online, *, active_desc: str | None = None) -> dict:
	raw = {
		"encryptJobId": f"job-{sid}",
		"jobName": f"Job-{sid}",
		"brandName": f"Company-{sid}",
		"salaryDesc": "20-30K",
		"cityName": "上海",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"welfareList": [],
		"bossOnline": online,
		"securityId": sid,
	}
	if active_desc is not None:
		raw["activeTimeDesc"] = active_desc
	return raw


def _page(*jobs, has_more=True):
	return {"code": 0, "zpData": {"hasMore": has_more, "jobList": list(jobs)}}


def _live_shaped_page(has_more=True):
	"""实测形态：15 条、无 activeTimeDesc，14 条在线（True / 1 混合），1 条离线。"""
	online_values = [True, 1] * 7
	jobs = [_job(f"on{i}", value) for i, value in enumerate(online_values)]
	jobs.append(_job("off", False))
	return _page(*jobs, has_more=has_more)


# ── bossOnline 归一化 ───────────────────────────────────────────────


@pytest.mark.parametrize("value", [True, 1, "1", " true "])
def test_is_boss_online_truthy_forms(value):
	assert is_boss_online(value) is True
	assert JobItem.from_api(_job("x", value)).boss_active == "在线"
	assert list_active_decision(_job("x", value), "3d") == ("pass", "在线")


@pytest.mark.parametrize("value", [False, 0, "0", "", None, 2, "yes"])
def test_is_boss_online_offline_forms(value):
	assert is_boss_online(value) is False
	assert JobItem.from_api(_job("x", value)).boss_active == "离线"


def test_online_level_never_needs_detail():
	assert list_active_decision(_job("x", False), "online") == ("reject", "离线")
	assert list_active_decision(_job("x", 0, active_desc="刚刚活跃"), "online") == ("pass", "刚刚活跃")
	assert list_active_decision(_job("x", 0, active_desc="今日活跃"), "online") == ("reject", "今日活跃")
	assert list_active_decision(_job("x", False), "3d") == ("detail", "")


def test_detail_filter_max_pages():
	assert detail_filter_max_pages(None, None) == 1
	assert detail_filter_max_pages(None, "online") == 1
	assert detail_filter_max_pages(None, "3d") == 3
	assert detail_filter_max_pages([("双休", ["双休"])], None) == 5
	assert detail_filter_max_pages([("双休", ["双休"])], "3d") == 5


def test_detail_channel_for():
	assert detail_channel_for({}) == "auto"
	assert detail_channel_for({"browser_source": "auto"}) == "auto"
	assert detail_channel_for({"cdp_url": "http://localhost:9222"}) == "browser"
	assert detail_channel_for({"browser_source": "existing-browser"}) == "browser"
	assert detail_channel_for(None) == "auto"


# ── 管线请求量 ──────────────────────────────────────────────────────


def test_active_online_on_live_shaped_list_makes_zero_detail_calls_and_one_page():
	client = FakeClient([_live_shaped_page(), _live_shaped_page()])
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="Python", city="上海"),
		max_pages=5,  # 调用方传大了也会被封顶到 1 页
		active="online",
	)
	assert client.detail_calls == []
	assert client.list_calls == 1
	assert result.stats.pages_scanned == 1
	assert result.stats.detail_requests == 0
	assert len(result.items) == 14
	assert "off" not in {item["security_id"] for item in result.items}
	assert all(item["boss_active"] == "在线" for item in result.items)
	assert result.stats.active_rejected == 1


def test_active_3d_only_details_offline_rows_sequentially_with_spacing():
	events: list[str] = []
	page = _page(
		_job("on1", True), _job("on2", 1), _job("on3", "1"),
		_job("off1", False), _job("off2", 0), _job("off3", None),
		has_more=False,
	)
	client = FakeClient(
		[page],
		cards={
			"off1": {"activeTimeDesc": "今日活跃", "postDescription": ""},
			"off2": {"activeTimeDesc": "本月活跃", "postDescription": ""},
			"off3": {"activeTimeDesc": "3日内活跃", "postDescription": ""},
		},
		events=events,
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"),
		max_pages=3,
		active="3d",
		before_detail_request=lambda: events.append("wait"),
	)
	# 在线行不查详情；离线行按列表顺序逐个查，每次前都等一次
	assert client.detail_calls == ["off1", "off2", "off3"]
	assert events == ["wait", "card:off1", "wait", "card:off2", "wait", "card:off3"]
	assert client.max_in_flight == 1
	assert {item["security_id"] for item in result.items} == {"on1", "on2", "on3", "off1", "off3"}
	assert result.stats.detail_requests == 3
	assert result.stats.active_rejected == 1


def test_active_3d_respects_hard_page_cap():
	pages = [_page(_job(f"off{i}", False)) for i in range(5)]
	cards = {f"off{i}": {"activeTimeDesc": "半年前活跃", "postDescription": ""} for i in range(5)}
	client = FakeClient(pages, cards=cards)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=5, active="3d",
	)
	assert result.stats.pages_scanned == 3
	assert client.list_calls == 3
	assert client.detail_calls == ["off0", "off1", "off2"]


def test_active_3d_stops_paging_once_default_target_reached():
	jobs = [_job(f"on{i}", True) for i in range(12)] + [_job("off", False)]
	client = FakeClient(
		[_page(*jobs), _live_shaped_page()],
		cards={"off": {"activeTimeDesc": "今日活跃", "postDescription": ""}},
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=3, active="3d",
	)
	assert client.list_calls == 1
	assert result.stats.pages_scanned == 1
	assert len(result.items) == 13


def test_active_3d_stops_paging_once_limit_reached():
	client = FakeClient(
		[_page(_job("off1", False), _job("on1", True)), _page(_job("off2", False))],
		cards={"off1": {"activeTimeDesc": "今日活跃", "postDescription": ""}},
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=3, limit=2, active="3d",
	)
	assert client.list_calls == 1
	assert client.detail_calls == ["off1"]
	assert len(result.items) == 2


def test_active_cdp_mode_uses_browser_job_card_only():
	client = BrowserFakeClient(
		[_page(_job("off1", False), _job("on1", True), has_more=False)],
		cards={"off1": {"activeTimeDesc": "今日活跃", "postDescription": ""}},
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=3, active="3d",
		detail_channel="browser",
	)
	assert client.browser_calls == ["off1"]
	assert {item["security_id"] for item in result.items} == {"off1", "on1"}


def test_active_with_welfare_also_runs_details_sequentially():
	events: list[str] = []
	client = FakeClient(
		[_page(_job("a", False), _job("b", False), _job("c", False), has_more=False)],
		cards={sid: {"activeTimeDesc": "今日活跃", "postDescription": "周末双休"} for sid in "abc"},
		events=events,
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"),
		welfare_conditions=[("双休", ["双休"])],
		active="3d",
		max_pages=5,
		before_detail_request=lambda: events.append("wait"),
	)
	assert client.max_in_flight == 1
	assert events == ["wait", "card:a", "wait", "card:b", "wait", "card:c"]
	assert len(result.items) == 3


def test_active_environment_risk_dict_stops_without_further_detail_calls():
	client = FakeClient(
		[_page(_job("off1", False), _job("off2", False), _job("off3", False))],
		cards={
			"off1": {"code": 37, "message": "环境异常"},
			"off2": {"activeTimeDesc": "今日活跃", "postDescription": ""},
			"off3": {"activeTimeDesc": "今日活跃", "postDescription": ""},
		},
	)
	with pytest.raises(SearchPipelinePlatformError) as exc_info:
		run_search_pipeline(
			client, FakeCache(), FakeLogger(),
			criteria=SearchFilterCriteria(query="go"), max_pages=3, active="3d",
		)
	assert exc_info.value.code == "ENVIRONMENT_RISK"
	assert client.detail_calls == ["off1"]
	assert client.list_calls == 1


def test_active_risk_exception_stops_without_further_detail_calls():
	client = FakeClient(
		[_page(_job("off1", False), _job("off2", False))],
		cards={
			"off1": AccountRiskError("BOSS 直聘风控拦截 (code 36)", is_cdp=True),
			"off2": {"activeTimeDesc": "今日活跃", "postDescription": ""},
		},
	)
	with pytest.raises(AccountRiskError):
		run_search_pipeline(
			client, FakeCache(), FakeLogger(),
			criteria=SearchFilterCriteria(query="go"), max_pages=3, active="3d",
		)
	assert client.detail_calls == ["off1"]


# ── 命令层 ──────────────────────────────────────────────────────────


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _invoke_search(args: list[str], result: SearchPipelineResult | None = None):
	with patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline, \
		patch("boss_agent_cli.commands.search.CacheStore") as mock_cache_cls, \
		patch("boss_agent_cli.commands.search.AuthManager"), \
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform_cls:
		_ctx_mock(mock_platform_cls)
		_ctx_mock(mock_cache_cls)
		mock_pipeline.return_value = result or SearchPipelineResult()
		outcome = CliRunner().invoke(cli, args)
	return outcome, mock_pipeline


def test_search_command_active_online_uses_single_page(tmp_path):
	outcome, mock_pipeline = _invoke_search(["--data-dir", str(tmp_path), "--json", "search", "Python", "--active", "online"])
	assert outcome.exit_code == 0, outcome.output
	kwargs = mock_pipeline.call_args.kwargs
	assert kwargs["active"] == "online"
	assert kwargs["max_pages"] == 1


def test_search_command_active_passes_detail_spacing_and_channel(tmp_path):
	outcome, mock_pipeline = _invoke_search(["--data-dir", str(tmp_path), "--json", "search", "Python", "--active", "3d"])
	assert outcome.exit_code == 0, outcome.output
	kwargs = mock_pipeline.call_args.kwargs
	assert callable(kwargs["before_detail_request"])
	assert kwargs["detail_channel"] == "auto"

	outcome, mock_pipeline = _invoke_search([
		"--data-dir", str(tmp_path), "--cdp-url", "http://localhost:9222",
		"--json", "search", "Python", "--active", "3d",
	])
	assert outcome.exit_code == 0, outcome.output
	assert mock_pipeline.call_args.kwargs["detail_channel"] == "browser"


def test_search_command_without_active_keeps_detail_defaults(tmp_path):
	outcome, mock_pipeline = _invoke_search([
		"--data-dir", str(tmp_path),
		"--json", "search", "Python", "--welfare", "双休", "--no-cache",
	])
	assert outcome.exit_code == 0, outcome.output
	kwargs = mock_pipeline.call_args.kwargs
	assert kwargs["max_pages"] == 5
	assert "before_detail_request" not in kwargs
	assert "detail_channel" not in kwargs


def test_search_command_cdp_welfare_uses_browser_channel_and_spacing(tmp_path):
	"""CDP 模式下福利兜底取详情也只走浏览器、串行并按 CrawlBudget 间隔。"""
	outcome, mock_pipeline = _invoke_search([
		"--data-dir", str(tmp_path), "--cdp-url", "http://localhost:9222",
		"--json", "search", "Python", "--welfare", "双休", "--no-cache",
	])
	assert outcome.exit_code == 0, outcome.output
	kwargs = mock_pipeline.call_args.kwargs
	assert kwargs["max_pages"] == 5
	assert callable(kwargs["before_detail_request"])
	assert kwargs["detail_channel"] == "browser"


def test_search_command_reports_detail_lookup_count(tmp_path):
	stats = SearchPipelineStats(pages_scanned=1, detail_requests=4)
	outcome, _ = _invoke_search(
		["--data-dir", str(tmp_path), "--json", "search", "Python", "--active", "3d"],
		SearchPipelineResult(stats=stats),
	)
	assert outcome.exit_code == 0, outcome.output
	hint = json.loads(outcome.output)["hints"]["active_filter"]
	assert hint["detail_lookups"] == 4
	assert "--active online" in hint["detail_note"]
