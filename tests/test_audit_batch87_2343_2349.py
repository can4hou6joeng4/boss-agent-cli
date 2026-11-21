"""批次 87 审计修复：导出边界、wizard 状态一致性与输入的确定性错误。

- chat 导出把超大整数 unread 按缺失处理，md/html 不再裸 ValueError；
- Markdown 分组标题与总计行转义平台字段，不能注入伪造章节；
- job HTML 的「共 N 条」只统计实际渲染的对象条目；
- list_recent 与 get_checkpoint 同口径拒绝 pending + completed 的断点；
- 招聘者候选人搜索的非字符串筛选项报参数错误，不静默 str() 后发平台；
- ai_assist 缺 prompt 报输入错误而不是 INTERNAL_ERROR；
- 无表头面试线索表的多行首记录参与判重（Issue #2343–#2349）。
"""

from __future__ import annotations

import pytest

from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.job_export import render_html_export
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	execute_recruiter_candidate_search,
)
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan("candidate", "zhipin", "job_search", {}, steps, "headless")


# ── Issue #2343：超大整数 unread 在 md/html 通道不得裸崩 ─────────────


@pytest.mark.parametrize("fmt", ["md", "html"])
def test_chat_export_treats_oversized_unread_as_missing(fmt: str) -> None:
	friends = [{
		"name": "张测试",
		"initiated_by": "对方主动",
		"msg_status": "未读",
		"last_msg": "hi",
		"unread": 10**5000,
	}]
	text = render_export(friends, fmt, None, None, {})

	assert "张测试" in text
	if fmt == "md":
		# 未读列留空，与 CSV 同口径。
		assert "|  | 未读 |" in text
	else:
		assert '<span class="unread">' not in text


def test_chat_export_still_renders_normal_unread() -> None:
	md = render_export(
		[{"name": "张测试", "initiated_by": "对方主动", "msg_status": "未读", "last_msg": "hi", "unread": 3}],
		"md",
		None,
		None,
		{},
	)
	assert "| 3 | 未读 |" in md


# ── Issue #2344：Markdown 标题与总计行转义平台字段 ──────────────────


def test_markdown_export_escapes_group_identity_in_heading_and_total() -> None:
	friend = {
		"name": "张测试",
		"brand_name": "示例公司",
		"unread": 0,
		"last_msg": "hi",
		"initiated_by": "我主动\n\n## 伪造标题\n[点我](https://evil.example)",
	}
	md = render_export([friend], "md", None, None, {"is_first": True})

	# 换行被压成空格，伪造章节不会出现在行首；链接语法被转义。
	assert "\n## 伪造标题" not in md
	assert "[点我](https://evil.example)" not in md
	assert "\\[点我\\](https://evil.example)" in md


# ── Issue #2345：job HTML 总数只统计实际渲染的条目 ─────────────────


def test_job_html_export_total_counts_renderable_items_only() -> None:
	html = render_html_export([None, "坏条目", 5, {"title": "后端工程师", "company": "示例公司"}])

	assert "共 1 条" in html
	assert "共 4 条" not in html
	assert "后端工程师" in html


def test_job_html_export_total_is_zero_when_nothing_renderable() -> None:
	html = render_html_export([None, "坏条目"])

	assert "共 0 条" in html


# ── Issue #2346：list_recent 与 get_checkpoint 同口径 ─────────────────


def test_list_recent_hides_run_with_completed_result_on_pending_step(tmp_path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("run-consistency", _plan("search"))
		store.update_step("run-consistency", "search", status="pending", result=StepResult({}).to_dict())

		assert store.list_recent() == []
		assert store.corrupt_run_ids == ["run-consistency"]
		# 与 --resume 的结论一致：该断点只能重建。
		with pytest.raises(CacheRecordInvalid, match="pending"):
			store.get_checkpoint("run-consistency")


# ── Issue #2347：招聘者候选人搜索的字符串筛选项走 strict ───────────


class _Recorder:
	"""记录 search_geeks 收到的参数，不发平台请求。"""

	def __init__(self) -> None:
		self.calls: list[tuple[str, dict[str, object]]] = []

	def search_geeks(self, query: str, **kwargs: object) -> dict[str, object]:
		self.calls.append((query, kwargs))
		return {"code": 0}


@pytest.mark.parametrize("bad", [False, 0, 1.5, {"a": 1}, ["x"]])
def test_recruiter_candidate_search_rejects_non_string_query(bad: object) -> None:
	platform = _Recorder()

	with pytest.raises(WorkflowInputError, match="query"):
		execute_recruiter_candidate_search(platform, {"query": bad})

	assert platform.calls == []


def test_recruiter_candidate_search_rejects_non_string_filters() -> None:
	platform = _Recorder()

	with pytest.raises(WorkflowInputError, match="city"):
		execute_recruiter_candidate_search(platform, {"query": "Python", "city": ["北京"]})

	assert platform.calls == []


def test_recruiter_candidate_search_keeps_valid_inputs() -> None:
	platform = _Recorder()

	execute_recruiter_candidate_search(platform, {"query": "Python", "city": "", "page": 1})

	[(query, kwargs)] = platform.calls
	assert query == "Python"
	# 空串与原先 _optional_str 同口径，仍按缺省传给平台。
	assert kwargs["city"] is None


# ── Issue #2348：ai_assist 缺 prompt 是输入错误 ──────────────────────


def test_ai_assist_missing_prompt_is_input_error_not_internal_error(tmp_path) -> None:
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	context = ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["ai_assist"](context, {"resume": "default"}, {})

	assert excinfo.value.code == "WIZARD_INPUT_REQUIRED"
	assert "prompt" in str(excinfo.value)


# ── Issue #2349：无表头线索表的多行首记录参与判重 ───────────────────


def test_interview_lead_dedup_sees_multiline_first_record_without_header(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	path = store.root / "interview-leads.csv"
	path.write_text('"nick\nline2",2026-01-01 10:00,reason\r\n', encoding="utf-8", newline="")

	assert store.append_interview_lead("nick\nline2", "2026-01-01 10:00", "reason") is False
	assert path.read_text(encoding="utf-8").count("nick") == 1


def test_interview_lead_dedup_sees_multiline_first_record_with_header(tmp_path) -> None:
	store = AutomationStore(tmp_path)
	store.append_interview_lead("nick\nline2", "2026-01-01 10:00", "reason")

	assert store.append_interview_lead("nick\nline2", "2026-01-01 10:00", "reason") is False
