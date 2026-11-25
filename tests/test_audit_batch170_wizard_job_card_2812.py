"""向导职位详情 / 本地采集简报 / 聊天方向的安全标量（Issue #2812）。"""

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


def _assert_no_repr(text: str) -> None:
	assert "{'" not in text
	assert "nan" not in text
	assert "inf" not in text
	assert "None" not in text
	assert "True" not in text
	assert "False" not in text


@pytest.mark.parametrize(
	"job",
	[
		{"title": "职位", "description": {"bad": 1}},
		{"title": "职位", "description": 10**5000},
		{"title": {"bad": 1}, "description": "正常 JD"},
		{"title": "职位", "company": {"name": "公司甲"}, "description": "正常 JD"},
		{"title": "职位", "skills": [{"bad": 1}, "Python"], "description": "正常 JD"},
		{"title": "职位", "address": {"bad": 1}, "description": "正常 JD"},
	],
	ids=["desc-dict", "desc-huge", "title-dict", "company-object", "skills-drift", "address-dict"],
)
def test_job_card_drift_does_not_crash(rendered: io.StringIO, job: dict[str, Any]) -> None:
	renderer.render_job_card_zh(job)

	_assert_no_repr(rendered.getvalue())


def test_job_card_text_is_kept(rendered: io.StringIO) -> None:
	job = {
		"title": "后端工程师",
		"salary": "20-30K",
		"company": "示例公司",
		"city": "北京",
		"experience": "3-5 年",
		"education": "本科",
		"boss_name": "招聘者甲",
		"boss_title": "技术负责人",
		"skills": ["Python", "SQL"],
		"description": "负责后端服务",
		"address": "朝阳区",
	}
	renderer.render_job_card_zh(job)

	text = rendered.getvalue()
	assert "后端工程师" in text
	assert "20-30K" in text
	assert "示例公司" in text
	assert "Python" in text
	assert "负责后端服务" in text
	assert "朝阳区" in text


def test_job_card_company_object_uses_name(rendered: io.StringIO) -> None:
	renderer.render_job_card_zh({"title": "职位", "company": {"name": "对象公司"}, "description": "JD"})

	assert "对象公司" in rendered.getvalue()


@pytest.mark.parametrize("page", [10**5000, {"p": 1}, float("nan")], ids=["huge", "dict", "nan"])
def test_crawl_job_brief_page_drift_does_not_crash(rendered: io.StringIO, page: Any) -> None:
	renderer.render_crawl_job_brief({"title": "职位", "crawl_page": page})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "采集页" not in text


def test_crawl_job_brief_text_is_kept(rendered: io.StringIO) -> None:
	job = {"title": "职位甲", "crawl_page": 3, "labels": ["急聘"], "company": {"name": "公司乙"}}
	renderer.render_crawl_job_brief(job, with_description=True)

	text = rendered.getvalue()
	assert "职位甲" in text
	assert "第 3 页" in text
	assert "急聘" in text
	assert "公司乙" in text


def test_crawl_job_brief_description_drift_falls_back(rendered: io.StringIO) -> None:
	renderer.render_crawl_job_brief({"title": "职位", "description": {"bad": 1}}, with_description=True)

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "本地暂无职位描述" in text


def test_crawl_job_brief_labels_drift(rendered: io.StringIO) -> None:
	renderer.render_crawl_job_brief({"title": "职位", "labels": [{"bad": 1}, "急聘", 10**5000]})

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert "急聘" in text


@pytest.mark.parametrize(
	"item, expected",
	[
		({"from": 42, "text": "正文"}, "42"),
		({"from": 10**5000, "text": "正文"}, "对方"),
		({"from": {"uid": 10**5000}, "text": "正文"}, "对方"),
		({"from": {"uid": 7}, "text": "正文"}, "7"),
		({"from": {"name": "候选人甲"}, "text": "正文"}, "候选人甲"),
		({"isSelf": True, "text": "正文"}, "我"),
		({"isSelf": {"x": 1}, "text": "正文"}, "对方"),
	],
	ids=["int", "huge-int", "uid-huge", "uid", "name", "self", "self-drift"],
)
def test_chat_direction_drift(rendered: io.StringIO, item: dict[str, Any], expected: str) -> None:
	run = {
		"status": "completed",
		"role": "candidate",
		"goal": "chat_history",
		"last_result": {"data": {"messages": [item]}},
	}
	renderer.render_run(run)

	text = rendered.getvalue()
	_assert_no_repr(text)
	assert expected in text
