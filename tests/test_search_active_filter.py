"""#442：boss search --active 按 HR 活跃度筛选。"""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_server import _build_args
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	SearchPipelineResult,
	SearchPipelineStats,
	active_desc_rank,
	list_active_decision,
	resolve_active_level,
	resolve_welfare_keywords,
	run_search_pipeline,
)


class FakeLogger:
	def __init__(self):
		self.messages: list[str] = []

	def info(self, message: str):
		self.messages.append(message)


class FakeCache:
	def __init__(self, descs: dict[str, str] | None = None):
		self.descs = descs or {}
		self.put_calls: list[tuple[str, str]] = []

	def is_greeted(self, security_id: str) -> bool:
		return False

	def get_job_desc(self, job_id: str) -> str | None:
		return self.descs.get(job_id)

	def put_job_desc(self, job_id: str, description: str) -> None:
		self.put_calls.append((job_id, description))


class FakeClient:
	name = "zhipin"

	def __init__(self, pages: list[dict], cards: dict[str, dict] | None = None):
		self.pages = list(pages)
		self.cards = cards or {}
		self.detail_calls: list[str] = []

	def search_jobs(self, query: str, **filters):
		return self.pages.pop(0)

	def is_success(self, response: dict) -> bool:
		return response.get("code", 0) == 0

	def parse_error(self, response: dict) -> tuple[str, str]:
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict):
		return response.get("zpData")

	def job_card(self, security_id: str, lid: str = ""):
		self.detail_calls.append(security_id)
		return {"code": 0, "zpData": {"jobCard": self.cards[security_id]}}


def _job(sid: str, *, active_desc: str | None = None, online: bool = False, welfare: list[str] | None = None) -> dict:
	raw = {
		"encryptJobId": f"job-{sid}",
		"jobName": f"Job-{sid}",
		"brandName": f"Company-{sid}",
		"salaryDesc": "20-30K",
		"cityName": "广州",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"welfareList": welfare or [],
		"bossOnline": online,
		"securityId": sid,
	}
	if active_desc is not None:
		raw["activeTimeDesc"] = active_desc
	return raw


def _page(*jobs, has_more=False):
	return {"code": 0, "zpData": {"hasMore": has_more, "jobList": list(jobs)}}


@pytest.mark.parametrize(
	("value", "level"),
	[
		("3d", "3d"), ("3day", "3d"), ("3日", "3d"), ("3天", "3d"), ("3DAYS", "3d"),
		("online", "online"), ("刚刚活跃", "online"), ("today", "today"), ("今日", "today"),
		("week", "week"), ("本周", "week"), ("2w", "2w"), ("两周", "2w"),
		("month", "month"), ("half-year", "half-year"), ("halfyear", "half-year"), ("半年", "half-year"),
	],
)
def test_resolve_active_level_aliases(value, level):
	assert resolve_active_level(value) == level


def test_resolve_active_level_rejects_unknown_and_ignores_empty():
	assert resolve_active_level(None) is None
	assert resolve_active_level("  ") is None
	with pytest.raises(ValueError, match="未知活跃度"):
		resolve_active_level("yesterday")


def test_active_desc_scale_is_ordered():
	ordered = ["刚刚活跃", "今日活跃", "3日内活跃", "本周活跃", "2周内活跃", "本月活跃", "近半年活跃", "半年前活跃"]
	ranks = [active_desc_rank(desc) for desc in ordered]
	assert ranks == sorted(ranks, reverse=True)
	assert active_desc_rank("在线") == active_desc_rank("刚刚活跃")
	assert active_desc_rank("4日内活跃") is None
	assert active_desc_rank(None) is None


def test_list_active_decision():
	assert list_active_decision(_job("a", active_desc="今日活跃"), "3d") == ("pass", "今日活跃")
	assert list_active_decision(_job("a", active_desc="本月活跃"), "3d") == ("reject", "本月活跃")
	assert list_active_decision(_job("a", active_desc="神秘活跃"), "3d") == ("unknown", "神秘活跃")
	assert list_active_decision(_job("a", online=True), "online") == ("pass", "在线")
	assert list_active_decision(_job("a"), "3d") == ("detail", "")


def test_job_item_exposes_boss_active_desc_and_keeps_boss_active():
	item = JobItem.from_api(_job("a", active_desc="本周活跃", online=False)).to_dict()
	assert item["boss_active"] == "离线"
	assert item["boss_active_desc"] == "本周活跃"
	assert JobItem.from_api(_job("b")).to_dict()["boss_active_desc"] == ""


def test_list_fields_decide_without_detail_calls():
	client = FakeClient([_page(
		_job("keep", active_desc="3日内活跃"),
		_job("drop", active_desc="本月活跃"),
		_job("online", online=True),
	)])
	result = run_search_pipeline(client, FakeCache(), FakeLogger(), criteria=SearchFilterCriteria(query="go"), active="3day")
	assert [item["security_id"] for item in result.items] == ["keep", "online"]
	assert result.items[0]["boss_active_desc"] == "3日内活跃"
	assert client.detail_calls == []
	assert result.stats.active_rejected == 1


def test_falls_back_to_job_card_when_list_has_no_desc():
	client = FakeClient(
		[_page(_job("fresh"), _job("stale"), _job("blank"))],
		cards={
			"fresh": {"activeTimeDesc": "刚刚活跃", "postDescription": ""},
			"stale": {"activeTimeDesc": "半年前活跃", "postDescription": ""},
			"blank": {"postDescription": ""},
		},
	)
	result = run_search_pipeline(client, FakeCache(), FakeLogger(), criteria=SearchFilterCriteria(query="go"), active="week")
	assert [item["security_id"] for item in result.items] == ["fresh"]
	assert result.items[0]["boss_active_desc"] == "刚刚活跃"
	assert sorted(client.detail_calls) == ["blank", "fresh", "stale"]
	assert result.stats.active_rejected == 1
	assert result.stats.active_unknown == 1
	assert result.active_unknown_descs == {"(空)": 1}


def test_unknown_list_desc_is_excluded_and_reported_without_detail_call():
	client = FakeClient([_page(_job("odd", active_desc="神秘活跃"))])
	result = run_search_pipeline(client, FakeCache(), FakeLogger(), criteria=SearchFilterCriteria(query="go"), active="3d")
	assert result.items == []
	assert client.detail_calls == []
	assert result.active_unknown_descs == {"神秘活跃": 1}


def test_combined_with_welfare_uses_one_detail_call_and_bypasses_desc_cache_for_active():
	welfare = [("双休", resolve_welfare_keywords("双休"))]
	client = FakeClient(
		[_page(
			_job("tag", welfare=["双休"]),               # 福利标签命中，只需补活跃度
			_job("desc"),                               # 福利和活跃度都要看详情
			_job("listed", active_desc="今日活跃"),      # 活跃度列表已知，福利靠缓存描述
		)],
		cards={
			"tag": {"activeTimeDesc": "本周活跃", "postDescription": ""},
			"desc": {"activeTimeDesc": "今日活跃", "postDescription": "周末双休"},
		},
	)
	cache = FakeCache(descs={"job-desc": "旧描述没有福利", "job-listed": "双休"})
	result = run_search_pipeline(
		client, cache, FakeLogger(),
		criteria=SearchFilterCriteria(query="go"),
		welfare_conditions=welfare,
		active="week",
	)
	by_sid = {item["security_id"]: item for item in result.items}
	assert set(by_sid) == {"tag", "desc", "listed"}
	assert by_sid["tag"]["welfare_match"] == "✅ 双休(标签)"
	assert by_sid["tag"]["boss_active_desc"] == "本周活跃"
	# 需要活跃度时不用缓存的旧描述，而是用新取到的详情
	assert by_sid["desc"]["welfare_match"] == "✅ 双休(描述)"
	assert by_sid["listed"]["welfare_match"] == "✅ 双休(描述)"
	assert sorted(client.detail_calls) == ["desc", "tag"]


def test_active_filter_turns_page_only_when_short_and_detail_was_needed():
	client = FakeClient(
		[
			_page(_job("p1"), has_more=True),
			_page(_job("p2", active_desc="今日活跃"), has_more=False),
		],
		cards={"p1": {"activeTimeDesc": "本月活跃", "postDescription": ""}},
	)
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=5, active="today",
	)
	assert [item["security_id"] for item in result.items] == ["p2"]
	assert result.stats.pages_scanned == 2


def test_active_filter_does_not_turn_page_when_list_decided_everything():
	client = FakeClient([
		_page(_job("p1", active_desc="本月活跃"), has_more=True),
		_page(_job("p2", active_desc="今日活跃"), has_more=False),
	])
	result = run_search_pipeline(
		client, FakeCache(), FakeLogger(),
		criteria=SearchFilterCriteria(query="go"), max_pages=5, active="today",
	)
	assert result.items == []
	assert result.stats.pages_scanned == 1
	assert len(client.pages) == 1


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


@patch("boss_agent_cli.commands.search.run_search_pipeline")
@patch("boss_agent_cli.commands.search.CacheStore")
@patch("boss_agent_cli.commands.search.AuthManager")
@patch("boss_agent_cli.commands.search.get_platform_instance")
def test_search_command_active_bypasses_cache_and_reports_hints(mock_platform_cls, mock_auth_cls, mock_cache_cls, mock_pipeline):
	_ctx_mock(mock_platform_cls)
	mock_cache = _ctx_mock(mock_cache_cls)
	stats = SearchPipelineStats(pages_scanned=2, active_rejected=3, active_unknown=1)
	mock_pipeline.return_value = SearchPipelineResult(
		items=[{"security_id": "s1", "job_id": "j1", "boss_active_desc": "今日活跃"}],
		total=1,
		stats=stats,
		active_unknown_descs={"神秘活跃": 1},
	)

	result = CliRunner().invoke(cli, ["--json", "search", "golang", "--active", "3日"])

	assert result.exit_code == 0, result.output
	mock_cache.get_search.assert_not_called()
	mock_cache.put_search.assert_not_called()
	kwargs = mock_pipeline.call_args.kwargs
	assert kwargs["active"] == "3d"
	assert kwargs["max_pages"] == 3
	parsed = json.loads(result.output)
	assert parsed["hints"]["active_filter"] == {
		"level": "3d",
		"rejected": 3,
		"unknown_excluded": 1,
		"unknown_descs": {"神秘活跃": 1},
		"note": "活跃度文案无法识别或详情未返回的职位已排除",
	}


def test_search_command_rejects_unknown_active_level():
	result = CliRunner().invoke(cli, ["--json", "search", "golang", "--active", "yesterday"])
	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_preset_stores_active_and_search_reads_it(tmp_path):
	runner = CliRunner()
	added = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "go-active", "golang", "--active", "3day"])
	assert added.exit_code == 0, added.output
	assert json.loads(added.output)["data"]["params"]["active"] == "3d"
	plain = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "preset", "add", "go-plain", "golang"])
	assert "active" not in json.loads(plain.output)["data"]["params"]

	with patch("boss_agent_cli.commands.search.run_search_pipeline") as mock_pipeline, \
		patch("boss_agent_cli.commands.search.AuthManager"), \
		patch("boss_agent_cli.commands.search.get_platform_instance") as mock_platform_cls:
		_ctx_mock(mock_platform_cls)
		mock_pipeline.return_value = SearchPipelineResult()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "search", "--preset", "go-active"])
	assert result.exit_code == 0, result.output
	assert mock_pipeline.call_args.kwargs["active"] == "3d"


def test_mcp_search_and_preset_forward_active():
	assert _build_args("boss_search", {"query": "go", "active": "3d"}) == ["search", "go", "--active", "3d"]
	assert _build_args("boss_preset_add", {"name": "n", "query": "go", "active": "week"}) == ["preset", "add", "n", "go", "--active", "week"]


def test_wizard_search_action_passes_active_and_validates():
	from boss_agent_cli.wizard.actions import execute_candidate_search
	from boss_agent_cli.wizard.runner import WorkflowActionError

	calls = []

	def fake_pipeline(*args, **kwargs):
		calls.append(kwargs)
		return SearchPipelineResult()

	execute_candidate_search(None, None, None, {"query": "go", "active": "3天"}, pipeline=fake_pipeline)
	assert calls[0]["active"] == "3d"
	assert calls[0]["max_pages"] == 3
	execute_candidate_search(None, None, None, {"query": "go"}, pipeline=fake_pipeline)
	assert "active" not in calls[1]
	with pytest.raises(WorkflowActionError) as exc:
		execute_candidate_search(None, None, None, {"query": "go", "active": "bad"}, pipeline=fake_pipeline)
	assert exc.value.code == "INVALID_PARAM"
