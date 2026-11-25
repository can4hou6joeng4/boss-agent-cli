"""向导其余面板与 CLI 的文本 / 字段口径一致（Issue #2807）。"""

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


def _run(goal: str, data: dict[str, Any]) -> dict[str, Any]:
	return {"status": "completed", "role": "candidate", "goal": goal, "last_result": {"data": data}}


def test_jobs_list_uses_platform_status_desc(rendered: io.StringIO) -> None:
	renderer.render_run(_run("jobs_list", {"jobList": [{"jobName": "后端工程师", "jobStatusDesc": "招聘中"}]}))

	assert "招聘中" in rendered.getvalue()


def test_last_messages_contact_id_fallback(rendered: io.StringIO) -> None:
	data = {
		"items": [{"friendId": 9, "lastMsg": "收到，明天聊"}],
		"messages": [{"friendId": 9, "last_msg": "收到，明天聊"}],
	}
	renderer.render_run(_run("last_messages", data))

	text = rendered.getvalue()
	assert "9" in text
	assert "未命名" not in text


@pytest.mark.parametrize(
	"value", [{"text": "你好"}, float("nan"), 10**5000], ids=["dict", "nan", "huge-int"]
)
def test_communication_last_msg_drift_is_not_rendered(rendered: io.StringIO, value: Any) -> None:
	renderer.render_run(_run("communication", {"items": [{"name": "甲", "lastMsg": value}]}))

	text = rendered.getvalue()
	assert "{'" not in text
	assert "nan" not in text
	if type(value) is int:
		assert len(text) < 1000


def test_resume_iso_time_uses_cli_label(rendered: io.StringIO) -> None:
	renderer.render_run(_run("resumes", {"items": [{"name": "简历", "updated_at": "2026-11-25T12:34:56+08:00"}]}))

	text = rendered.getvalue()
	assert "2026-11-25 12:34" in text
	assert "T12:34:56" not in text


def test_candidates_accepts_position_name(rendered: io.StringIO) -> None:
	renderer.render_run(_run("candidates", {"items": [{"name": "候选人丙", "positionName": "后端工程师"}]}))

	assert "后端工程师" in rendered.getvalue()


def test_digest_preview_avoids_python_repr_and_scalar_drift(rendered: io.StringIO) -> None:
	data = {
		"new_match_count": True,
		"follow_up_count": float("inf"),
		"new_matches": [{"title": "职位", "company": {"name": "公司"}}],
	}
	renderer.render_run(_run("digest", data))

	text = rendered.getvalue()
	assert "公司" in text
	assert "{'" not in text
	assert "True" not in text
	assert "inf" not in text
