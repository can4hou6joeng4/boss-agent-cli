"""向导非列表面板的安全标量口径（Issue #2809）。"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.wizard import renderer


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def _run(goal: str, data: dict[str, Any], *, status: str = "completed", **last: Any) -> dict[str, Any]:
	return {"status": status, "role": "candidate", "goal": goal, "last_result": {"data": data, **last}}


def _assert_no_repr(text: str) -> None:
	assert "{'" not in text
	assert "nan" not in text
	assert "inf" not in text
	assert "None" not in text
	assert "True" not in text
	assert "False" not in text


def test_digest_summary_drift_is_not_rendered(rendered: io.StringIO) -> None:
	renderer.render_run(_run("digest", {"summary": {"secret": 1}, "new_match_count": 2}))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "新匹配" in text


def test_digest_summary_text_is_kept(rendered: io.StringIO) -> None:
	renderer.render_run(_run("digest", {"summary": "今天有 2 个新匹配", "new_match_count": 2}))

	assert "今天有 2 个新匹配" in rendered.getvalue()


def test_ai_assist_result_and_resume_drift(rendered: io.StringIO) -> None:
	renderer.render_run(_run("ai_assist", {"result": {"a": 1}, "resume": {"b": 2}}))

	_assert_no_repr(rendered.getvalue())


def test_ai_assist_result_text_is_kept(rendered: io.StringIO) -> None:
	renderer.render_run(_run("ai_assist", {"result": "建议突出项目经验", "resume": "后端.pdf"}))

	text = rendered.getvalue()
	assert "建议突出项目经验" in text
	assert "后端.pdf" in text


def test_watch_batch_drift_keeps_failure_marker(rendered: io.StringIO) -> None:
	data = {
		"mode": "all",
		"watches": [
			{"name": {"x": 1}, "new_count": True},
			{"name": float("nan"), "error": {"y": 2}},
			{"name": "监控丙", "new_count": 3, "error": "参数损坏"},
		],
	}
	renderer.render_run(_run("watch", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "监控丙" in text
	assert "参数损坏" in text
	assert "失败" in text


def test_watch_batch_skipped_names_drift(rendered: io.StringIO) -> None:
	renderer.render_run(_run("watch", {"mode": "all", "watches": [], "skipped": [{"a": 1}, "监控丁"]}))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "未命名" in text
	assert "监控丁" in text


def test_watch_single_result_counts_drift(rendered: io.StringIO) -> None:
	data = {"action": "add", "name": {"n": 1}, "new_count": float("inf"), "total": True}
	renderer.render_run(_run("watch", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "新发现" in text
	assert "命中" in text


def test_watch_single_result_text_is_kept(rendered: io.StringIO) -> None:
	data = {"action": "add", "name": "新监控", "new_count": 4, "total": 9}
	renderer.render_run(_run("watch", data))

	text = rendered.getvalue()
	assert "新监控" in text
	assert "4" in text
	assert "9" in text


def test_export_fields_drift(rendered: io.StringIO) -> None:
	data = {"path": {"p": 1}, "format": {"f": 2}, "count": True, "items": [{"title": "职位"}]}
	renderer.render_run(_run("export", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "条数" in text


def test_export_fields_text_is_kept(rendered: io.StringIO) -> None:
	data = {"path": "/tmp/demo/jobs.csv", "format": "csv", "count": 12, "items": [{"title": "职位"}]}
	renderer.render_run(_run("export", data))

	text = rendered.getvalue()
	assert "/tmp/demo/jobs.csv" in text
	assert "csv" in text
	assert "12" in text


def test_waiting_input_reason_and_actions_drift(rendered: io.StringIO) -> None:
	run = {
		"status": "waiting_input",
		"role": "candidate",
		"goal": "crawl_start",
		"last_result": {
			"data": {"error": {"bad": 1}, "run_id": {"r": 1}},
			"operator_actions": [{"a": 1}, "在浏览器完成验证", {"b": 2}],
		},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "需要补充信息或人工处理后才能继续" in text
	assert "在浏览器完成验证" in text


def test_waiting_input_next_action_text_is_kept(rendered: io.StringIO) -> None:
	run = {
		"status": "waiting_input",
		"role": "candidate",
		"goal": "crawl_start",
		"last_result": {
			"data": {"error": "需要验证码", "run_id": "run-2"},
			"next_action": "boss wizard --resume run-2",
			"operator_actions": ["在浏览器完成验证"],
		},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	assert "需要验证码" in text
	assert "run-2" in text
	assert "命令行恢复：boss wizard --resume run-2" in text


def test_fallback_panel_fields_drift(rendered: io.StringIO) -> None:
	data = {"result": {"a": 1}, "summary": {"b": 2}, "path": {"c": 3}}
	renderer.render_run(_run("unknown_goal", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "任务状态" in text


def test_fallback_panel_text_is_kept(rendered: io.StringIO) -> None:
	data = {"result": "已完成", "summary": "摘要", "path": "/tmp/demo/out.txt"}
	renderer.render_run(_run("unknown_goal", data))

	text = rendered.getvalue()
	assert "已完成" in text
	assert "摘要" in text
	assert "/tmp/demo/out.txt" in text


def test_crawl_summary_fields_drift(rendered: io.StringIO) -> None:
	data = {
		"status": "running",
		"jobs_seen": 0,
		"query": {"q": 1},
		"city_code": {"c": 1},
		"pages_completed": {"p": 1},
		"next_page": {"n": 1},
		"details_pending": {"d": 1},
		"details_completed": {"d": 2},
		"error": {"e": 1},
		"run_id": {"r": 1},
		"jobs": [{"title": {"bad": 1}, "jobName": "后端工程师", "company": {"name": "公司甲"}}],
	}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "后端工程师" in text
	assert "公司甲" in text
	assert "完成 0" in text


def test_crawl_summary_text_is_kept(rendered: io.StringIO) -> None:
	data = {
		"status": "running",
		"jobs_seen": 0,
		"query": "后端",
		"pages_completed": 3,
		"next_page": 4,
		"details_pending": 1,
		"details_completed": 5,
		"error": "限流",
		"run_id": "run-1",
	}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	assert "后端" in text
	assert "3 页" in text
	assert "4" in text
	assert "限流" in text
	assert "完成 5" in text
	assert "待补 1" in text
	assert "run-1" in text


def test_crawl_artifacts_drift(rendered: io.StringIO) -> None:
	data = {
		"status": "running",
		"output_paths": {"jobs": {"bad": 1}, "csv": 10**5000, "nul": "a\x00b", "report": "/tmp/demo/report.csv"},
	}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "report.csv" in text


def test_crawl_sample_titles_drift(rendered: io.StringIO) -> None:
	data = {"status": "running", "sample_titles": [{"bad": 1}, "职位甲 · 公司甲"]}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "职位甲" in text
	assert "公司甲" in text


def test_failed_run_error_drift_does_not_crash(rendered: io.StringIO) -> None:
	renderer.render_run({"status": "failed", "role": "candidate", "goal": "crawl_start", "error": "boom"})

	text = rendered.getvalue()
	assert "操作未完成" in text
	_assert_no_repr(text)


def test_failed_run_error_fields_drift(rendered: io.StringIO) -> None:
	run = {
		"status": "failed",
		"role": "candidate",
		"goal": "crawl_start",
		"error": {"code": {"c": 1}, "message": {"m": 1}, "recovery_action": {"r": 1}},
	}
	renderer.render_run(run)

	_assert_no_repr(rendered.getvalue())


def test_render_event_attempt_drift(rendered: io.StringIO) -> None:
	renderer.render_event("step_retrying", "crawl_start", {"attempt": {"a": 1}})
	renderer.render_event("step_retrying", "crawl_start", {"attempt": 10**5000})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert text.count("第 ? 次") == 2


@pytest.mark.parametrize("value", [10**5000, {"a": 1}, float("nan"), True], ids=["huge-int", "dict", "nan", "bool"])
def test_label_helpers_accept_drift(value: Any) -> None:
	assert renderer.status_label(value) == "状态未知"
	assert renderer.step_label(value) in {"处理任务步骤"}
	assert renderer.goal_label("candidate", value) == "自定义事项"
	assert "{" not in renderer.error_message("UNKNOWN_CODE", value)
	assert "{" not in (renderer.recovery_message("NETWORK_ERROR", value) or "")


def test_error_message_keeps_platform_text() -> None:
	assert renderer.error_message("NETWORK_ERROR", "平台返回了中文原因") == "平台返回了中文原因"


def test_crawl_summary_huge_jobs_seen_does_not_crash(rendered: io.StringIO) -> None:
	data = {"status": "completed", "jobs_seen": 10**5000, "jobs": [{"title": "职位"}]}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "职位预览" in text


def test_waiting_input_huge_jobs_seen_does_not_crash(rendered: io.StringIO) -> None:
	run = {
		"status": "waiting_input",
		"role": "candidate",
		"goal": "crawl_start",
		"last_result": {"data": {"error": "需要验证码", "jobs_seen": 10**5000}},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "需要验证码" in text


def test_action_panel_fields_drift(rendered: io.StringIO) -> None:
	data = {
		"security_id": {"s": 1},
		"job_id": 10**5000,
		"friend_id": True,
		"label": {"l": 1},
		"status": {"st": 1},
	}
	renderer.render_run(_run("greet", data))

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "操作成功" in text


def test_action_panel_text_is_kept(rendered: io.StringIO) -> None:
	data = {"security_id": "sid-123456789012345", "job_id": "job-1", "friend_id": 9, "label": "已沟通", "status": "成功"}
	renderer.render_run(_run("greet", data))

	text = rendered.getvalue()
	assert "已沟通" in text
	assert "job-1" in text
	assert "成功" in text


def test_crawl_checkpoint_resume_command_is_kept(rendered: io.StringIO) -> None:
	run = {
		"status": "waiting_input",
		"role": "candidate",
		"goal": "crawl_status",
		"effective_completed": True,
		"last_result": {
			"data": {"status": "running", "checkpoint": {"resume_command": "boss wizard --resume run-9"}},
		},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	assert "boss wizard --resume run-9" in text
	assert "下一步" in text


def test_crawl_artifacts_control_chars_are_dropped(rendered: io.StringIO) -> None:
	data = {"status": "running", "output_paths": {"nul": "a\x00b", "report": "/tmp/demo/report.csv"}}
	renderer.render_run(_run("crawl_status", data))

	text = rendered.getvalue()
	assert "nul" not in text
	assert "report.csv" in text


def test_boolean_flags_require_real_booleans(rendered: io.StringIO) -> None:
	run = {
		"status": "waiting_input",
		"role": "candidate",
		"goal": "crawl_start",
		"effective_completed": {"x": 1},
		"last_result": {"data": {"error": "需要验证码", "browser_kept_open": {"x": 1}}},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	assert "等待继续" in text
	assert "采集用 Chrome 已保持打开" not in text


def test_removed_flag_drift_is_not_rendered(rendered: io.StringIO) -> None:
	renderer.render_run(_run("watch", {"action": "add", "name": "监控", "removed": {"x": 1}}))

	assert "已删除" not in rendered.getvalue()


def test_failed_run_string_error_is_kept(rendered: io.StringIO) -> None:
	renderer.render_run({"status": "failed", "role": "candidate", "goal": "crawl_start", "error": "boom"})

	text = rendered.getvalue()
	assert "boom" in text
	assert "网络请求失败" not in text
