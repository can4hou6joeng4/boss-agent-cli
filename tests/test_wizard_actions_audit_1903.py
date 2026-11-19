"""wizard actions 的标签、run_id、消息输入与错误指引边界（Issue #1903–#1909）。"""

from __future__ import annotations

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlOutcome
from boss_agent_cli.output import Logger
from boss_agent_cli.services.labels import LABEL_MAP, parse_label_id
from boss_agent_cli.wizard.actions import (
	ActionContext,
	_bool_input,
	_candidate_greet,
	_candidate_mark,
	_crawl_resume,
	_crawl_start,
	_crawl_status,
	_explicit_crawl_run_id,
	_int_input,
	_optional_str_strict,
	_positive_int,
	_recruiter_reply,
	_required_str,
)
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path, *, candidate=None) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=(
			lambda name, auth, delay, cdp_url, browser_source=None: candidate if candidate is not None else None
		),
	)


class _FakeCandidate:
	def __init__(self) -> None:
		self.calls: list[tuple] = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def friend_list(self, *, page=1):
		return {
			"code": 0,
			"data": {
				"result": [{"uid": 10001, "securityId": "sid-1", "name": "甲", "friendSource": 0}],
				"hasMore": False,
			},
		}

	def friend_label(self, uid, label_id, friend_source=0, remove=False):
		self.calls.append(("friend_label", uid, label_id, friend_source, remove))
		return {"code": 0, "data": {"updated": True}}

	def greet(self, security_id, job_id, message=""):
		self.calls.append(("greet", security_id, job_id, message))
		return {"code": 0, "data": {"greeted": True}}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "UNKNOWN", "未识别"


# ── Issue #1905：标签映射单一真源与数字 ID 成员校验 ──────────────────


def test_label_map_includes_every_cli_label() -> None:
	assert LABEL_MAP["已交换电话"] == 5
	assert LABEL_MAP["已交换微信"] == 6
	assert LABEL_MAP["牛人发起"] == 8


@pytest.mark.parametrize("label", ["999", "①", "未知标签", "  "])
def test_parse_label_id_rejects_unknown_values(label: str) -> None:
	assert parse_label_id(label) is None


def test_parse_label_id_accepts_names_and_ids() -> None:
	assert parse_label_id("已交换电话") == 5
	assert parse_label_id("11") == 11


@pytest.mark.parametrize("label", ["999", "①", "未知标签"])
def test_candidate_mark_rejects_unknown_label_before_platform_call(tmp_path, label: str) -> None:
	platform = _FakeCandidate()

	with pytest.raises(WorkflowActionError) as excinfo:
		_candidate_mark(_context(tmp_path, candidate=platform), {"security_id": "sid-1", "label": label}, {})

	assert excinfo.value.code == "INVALID_PARAM"
	assert excinfo.value.recovery_action
	assert platform.calls == []


def test_candidate_mark_accepts_cli_only_label_name(tmp_path) -> None:
	platform = _FakeCandidate()

	_candidate_mark(_context(tmp_path, candidate=platform), {"security_id": "sid-1", "label": "已交换电话"}, {})

	assert platform.calls == [("friend_label", "10001", 5, 0, False)]


@pytest.mark.parametrize("label", [{"name": "收藏"}, ["收藏"], 5, False])
def test_candidate_mark_rejects_non_string_label(tmp_path, label: object) -> None:
	platform = _FakeCandidate()

	with pytest.raises(WorkflowInputError):
		_candidate_mark(_context(tmp_path, candidate=platform), {"security_id": "sid-1", "label": label}, {})

	assert platform.calls == []


# ── Issue #1903：plan 里的 run_id=null 不能覆盖已绑定的内部 run_id ────


class _RecordingService:
	def __init__(self) -> None:
		self.created = 0
		self.resumed: list[str] = []

	def create_and_run(self, settings):
		self.created += 1
		return _outcome()

	def resume(self, run_id, **kwargs):
		self.resumed.append(run_id)
		return _outcome()


def _outcome() -> CrawlOutcome:
	return CrawlOutcome(
		run_id="run-1",
		status="completed",
		next_page=1,
		pages_completed=1,
		jobs_seen=0,
		detail_checks=0,
		output_paths={},
		hooks=(),
		error="",
	)


def _patch_service(monkeypatch) -> _RecordingService:
	service = _RecordingService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda *args, **kwargs: service)
	return service


def test_crawl_start_resumes_bound_run_when_plan_has_null_run_id(tmp_path, monkeypatch) -> None:
	service = _patch_service(monkeypatch)

	_crawl_start(
		_context(tmp_path),
		{"query": "python", "city": "北京", "run_id": None},
		{"crawl_start": {"inner_run_id": "inner-r1"}},
	)

	assert service.resumed == ["inner-r1"]
	assert service.created == 0


def test_crawl_start_explicit_crawl_run_id_wins_over_null_run_id(tmp_path, monkeypatch) -> None:
	service = _patch_service(monkeypatch)

	_crawl_start(
		_context(tmp_path),
		{"query": "python", "city": "北京", "run_id": None, "crawl_run_id": "explicit-r2"},
		{},
	)

	assert service.resumed == ["explicit-r2"]
	assert service.created == 0


# ── Issue #1904：crawl_resume 与断点输入一致性校验 ────────────────────


def _seed_run(tmp_path, params: dict) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-old", params, str(tmp_path / "crawl" / "run-old"))


def test_crawl_resume_rejects_conflicting_query(tmp_path, monkeypatch) -> None:
	_seed_run(tmp_path, {"query": "old-keyword", "city_code": "101010100"})
	service = _patch_service(monkeypatch)

	with pytest.raises(WorkflowActionError) as excinfo:
		_crawl_resume(
			_context(tmp_path),
			{"run_id": "run-old", "query": "new-keyword", "city": "北京"},
			{},
		)

	assert excinfo.value.code == "INVALID_PARAM"
	assert "new-keyword" in str(excinfo.value)
	assert service.resumed == []


@pytest.mark.parametrize("query", [["new-keyword"], {"q": "new-keyword"}, 42])
def test_crawl_resume_rejects_non_string_query(tmp_path, monkeypatch, query: object) -> None:
	_seed_run(tmp_path, {"query": "old-keyword", "city_code": "101010100"})
	service = _patch_service(monkeypatch)

	with pytest.raises(WorkflowActionError) as excinfo:
		_crawl_resume(_context(tmp_path), {"run_id": "run-old", "query": query}, {})

	assert excinfo.value.code == "INVALID_PARAM"
	assert service.resumed == []


def test_crawl_resume_matching_inputs_still_resumes(tmp_path, monkeypatch) -> None:
	_seed_run(tmp_path, {"query": "old-keyword", "city_code": "101010100"})
	service = _patch_service(monkeypatch)

	_crawl_resume(
		_context(tmp_path),
		{"run_id": "run-old", "query": "old-keyword", "city": "北京"},
		{},
	)

	assert service.resumed == ["run-old"]


# ── Issue #1906：发给真人的消息必须是字符串 ──────────────────────────


@pytest.mark.parametrize("bad", [["你好", "谢谢"], {"text": "你好"}, 0, False, 1.5])
def test_required_str_rejects_non_string(bad: object) -> None:
	with pytest.raises(WorkflowInputError):
		_required_str({"message": bad}, "message")


def test_optional_str_strict_keeps_missing_and_null_empty() -> None:
	assert _optional_str_strict({}, "message") is None
	assert _optional_str_strict({"message": None}, "message") is None
	assert _optional_str_strict({"message": ""}, "message") == ""
	assert _optional_str_strict({"message": "你好"}, "message") == "你好"


@pytest.mark.parametrize("bad", [["你好", "谢谢"], {"text": "你好"}, 0, False])
def test_recruiter_reply_rejects_non_string_message(bad: object) -> None:
	with pytest.raises(WorkflowInputError):
		_recruiter_reply(_context(None), {"friend_id": 1, "message": bad}, {})


@pytest.mark.parametrize("bad", [0, False, ["你好"]])
def test_candidate_greet_rejects_non_string_message_before_platform(tmp_path, bad: object) -> None:
	platform = _FakeCandidate()

	with pytest.raises(WorkflowInputError):
		_candidate_greet(
			_context(tmp_path, candidate=platform),
			{"security_id": "sid-1", "job_id": "job-1", "message": bad},
			{},
		)

	assert platform.calls == []


# ── Issue #1907：错误码登记表兜底 recovery_action ────────────────────


def test_workflow_action_error_fills_recovery_action_from_registry() -> None:
	error = WorkflowActionError("CONTACT_NOT_FOUND", "未找到该联系人")

	assert error.recovery_action == "boss chat"


def test_explicit_recovery_action_is_not_replaced() -> None:
	error = WorkflowActionError("CONTACT_NOT_FOUND", "未找到该联系人", recovery_action="自定义指引")

	assert error.recovery_action == "自定义指引"


def test_crawl_status_missing_run_has_readable_message_and_recovery(tmp_path) -> None:
	with pytest.raises(WorkflowActionError) as excinfo:
		_crawl_status(_context(tmp_path), {"run_id": "missing-run"}, {})

	error = excinfo.value
	assert error.code == "CRAWL_RUN_NOT_FOUND"
	assert "missing-run" in str(error)
	assert "'run_id'" not in str(error)
	assert error.recovery_action is not None
	assert "crawl status" in error.recovery_action


# ── Issue #1908：run_id 与 crawl_run_id 冲突必须点名 ─────────────────


def test_explicit_crawl_run_id_conflict_is_rejected() -> None:
	with pytest.raises(WorkflowActionError) as excinfo:
		_explicit_crawl_run_id({"run_id": "run-a", "crawl_run_id": "run-b"})

	assert excinfo.value.code == "INVALID_PARAM"
	assert "run-a" in str(excinfo.value)
	assert "run-b" in str(excinfo.value)


def test_explicit_crawl_run_id_accepts_duplicate_values() -> None:
	assert _explicit_crawl_run_id({"run_id": "run-a", "crawl_run_id": " run-a "}) == "run-a"


# ── Issue #1909：类型错误消息截断超长输入 ────────────────────────────


def test_int_input_truncates_huge_value() -> None:
	with pytest.raises(WorkflowInputError) as excinfo:
		_int_input({"page": "9" * 5000}, "page", 1, minimum=1)

	message = str(excinfo.value)
	assert len(message) < 300
	assert "共 5002 字符" in message


def test_bool_input_truncates_huge_value() -> None:
	with pytest.raises(WorkflowInputError) as excinfo:
		_bool_input({"remove": "x" * 5000}, "remove")

	assert len(str(excinfo.value)) < 300


def test_positive_int_truncates_huge_value() -> None:
	with pytest.raises(WorkflowActionError) as excinfo:
		_positive_int(["x"] * 1000, name="pages")

	assert len(str(excinfo.value)) < 300
