"""search / wizard 的页码证据必须采信平台响应页，而不是回显请求参数（Issue #3118）。"""

import json
import shlex
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import SearchFilterCriteria, run_search_pipeline
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS


def _job():
	return {"job_id": "j1", "title": "Go 开发", "company": "TestCo", "salary": "20K", "city": "北京", "security_id": "sec_001", "greeted": False}


def _invoke_search(tmp_path, *, request_page=2, cached=None, reported_page=4):
	with (
		patch("boss_agent_cli.commands.search.AuthManager"),
		patch("boss_agent_cli.commands.search.get_platform_instance"),
		patch("boss_agent_cli.commands.search.CacheStore") as cache_cls,
		patch("boss_agent_cli.commands.search.run_search_pipeline") as pipeline,
		patch("boss_agent_cli.commands.search.try_save_index", return_value=None),
	):
		cache = cache_cls.return_value
		cache.__enter__ = lambda _self: cache
		cache.__exit__ = lambda _self, *_args: None
		cache.get_search.return_value = cached
		cache.is_greeted.return_value = False
		pipeline.return_value = SimpleNamespace(
			items=[_job()], has_more=True, has_more_reported=True,
			last_page=request_page, last_page_reported=reported_page, total=1,
			stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
		)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "Go", "--page", str(request_page)])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def _next_page(payload):
	actions = [a for a in payload["hints"]["next_actions"] if "search" in a and "--page" in a]
	assert len(actions) == 1, payload["hints"]["next_actions"]
	return shlex.split(actions[0])[-1]


def test_cli_uses_platform_page(tmp_path):
	payload = _invoke_search(tmp_path)
	assert payload["pagination"]["page"] == 4
	assert _next_page(payload) == "5"


def test_invalid_platform_page_falls_back_to_scanned_page(tmp_path):
	"""平台页码类型漂移时不能把非法值放进信封，也不该退回起始请求页。"""
	payload = _invoke_search(tmp_path, request_page=3, reported_page="4")
	assert payload["pagination"]["page"] == 3
	assert _next_page(payload) == "4"


def test_trusted_page_cap_stops_next_hint(tmp_path):
	payload = _invoke_search(tmp_path, reported_page=MAX_TRUSTED_PAGE)
	assert payload["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert not [a for a in payload["hints"]["next_actions"] if "search" in a and "--page" in a]


def test_cached_hint_uses_cached_page(tmp_path):
	cached = json.dumps({
		"data": [_job()],
		"pagination": {"page": 4, "has_more": True, "has_more_source": "platform_hasMore", "total": 1, "total_kind": "returned_page_count"},
	})
	payload = _invoke_search(tmp_path, request_page=1, cached=cached)
	assert payload["pagination"]["page"] == 4
	assert _next_page(payload) == "5"


class _Platform:
	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None


class _WizardResult:
	items = [_job()]
	has_more = True
	has_more_reported = True
	total = 1
	last_page = 2
	last_page_reported = 4


def test_wizard_uses_platform_page(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", lambda *a, **k: _WizardResult())
	ctx = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger(), candidate_factory=lambda *a, **k: _Platform())
	step = DEFAULT_ACTIONS["candidate_search"](ctx, {"query": "Go", "page": 2}, {})
	assert step.data["pagination"]["page"] == 4


class _FakeLogger:
	def info(self, message):
		pass


class _FakeCache:
	def is_greeted(self, security_id="", *, job_id="", account=""):
		return False

	def get_job_desc(self, security_id):
		return None


class _FakeClient:
	name = "zhipin"

	def __init__(self, page):
		self.page = page

	def search_jobs(self, query, **filters):
		return self.page

	def is_success(self, response):
		return True

	def parse_error(self, response):
		return "UNKNOWN", ""


def _raw_job():
	return {
		"encryptJobId": "j1", "jobName": "Go", "brandName": "TestCo",
		"salaryDesc": "20K", "cityName": "北京", "areaDistrict": "海淀",
		"jobExperience": "3-5年", "jobDegree": "本科", "skills": ["Go"],
		"welfareList": [], "brandIndustry": "互联网", "brandScaleName": "100-499人",
		"brandStageName": "A轮", "bossName": "李女士", "bossTitle": "HR",
		"bossOnline": True, "securityId": "sec_001", "lid": "",
	}


def test_pipeline_keeps_platform_page():
	client = _FakeClient({"zpData": {"page": 4, "hasMore": True, "jobList": [_raw_job()]}})
	result = run_search_pipeline(
		client, _FakeCache(), _FakeLogger(), criteria=SearchFilterCriteria(query="Go"),
		start_page=2, max_pages=1,
	)
	assert result.last_page_reported == 4
