"""向导结果菜单标签与回填输入的安全标量（Issue #2837）。"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.wizard.prompts import (
	MenuOption,
	_candidate_friend_follow_up,
	_candidate_job_follow_up,
	_candidate_resume_follow_up,
	_candidate_watch_follow_up,
	_is_crawl_local_job,
	_recruiter_follow_up,
	_result_menu_options,
)


class _ScriptedMenu:
	def __init__(self, selections: list[str], texts: list[str] | None = None) -> None:
		self._selections = iter(selections)
		self._texts = iter(texts or [])
		self.titles: list[str] = []
		self.options: list[list[MenuOption]] = []

	def select(
		self,
		title: str,
		options: Any,
		*,
		default: str | None = None,
		allow_back: bool = True,
		allow_exit: bool = True,
		clear_before: bool = True,
	) -> str:
		self.titles.append(title)
		self.options.append(list(options))
		return next(self._selections)

	def text(self, label: str, *, default: str = "", required: bool = True) -> str:
		return next(self._texts)


def _displayed(options: list[MenuOption]) -> str:
	return " ".join(f"{option.label} {option.description}" for option in options)


def test_job_menu_container_and_non_finite_fields_fall_back() -> None:
	options = _result_menu_options(
		[{"title": {"a": 1}, "company": ["x"], "salary": float("nan"), "city": "北京"}],
		kind="job",
	)

	assert options[0].label == "1  未命名职位"
	assert options[0].description == "公司未注明 · 北京"
	assert "{'" not in _displayed(options)
	assert "nan" not in _displayed(options)


def test_job_menu_container_title_falls_back_to_job_id() -> None:
	options = _result_menu_options([{"title": {"a": 1}, "job_id": "job-1"}], kind="job")

	assert "job-1" in options[0].label


def test_huge_integer_title_and_source_do_not_raise() -> None:
	options = _result_menu_options([{"title": 10**5000, "company": 10**5000}], kind="job")

	assert options[0].label == "1  未命名职位"
	assert options[0].description == "公司未注明"
	assert _is_crawl_local_job({"source": 10**5000}) is False


def test_watch_menu_container_query_does_not_swallow_fallback() -> None:
	options = _result_menu_options(
		[{"name": "监控", "params": {"query": {"bad": 1}}, "query": "后端"}],
		kind="watch",
	)

	assert "后端" in options[0].description


def test_resume_and_watch_follow_up_reject_unusable_names() -> None:
	menu = _ScriptedMenu([])

	assert _candidate_resume_follow_up(menu, {"name": {"a": 1}}, "zhipin") is None
	assert _candidate_resume_follow_up(menu, {"name": 10**5000}, "zhipin") is None
	assert _candidate_watch_follow_up(menu, {"name": ["x"]}, "zhipin") is None
	assert menu.titles == []


def test_job_follow_up_sanitizes_ids_and_description() -> None:
	menu = _ScriptedMenu(["done"], [])
	result = _candidate_job_follow_up(
		menu,
		{
			"source": "crawl",
			"security_id": {"bad": 1},
			"job_id": 10**5000,
			"description": 10**5000,
		},
		"zhipin",
	)

	assert result is None
	assert [option.value for option in menu.options[0]] == ["local_brief", "done"]
	assert _candidate_job_follow_up(_ScriptedMenu(["done"]), {"security_id": 10**5000}, "zhipin") is None


def test_friend_follow_up_requires_usable_security_id() -> None:
	menu = _ScriptedMenu(["done"])

	assert _candidate_friend_follow_up(menu, {"security_id": {"bad": 1}, "gid": 42}, "zhipin") is None
	assert [option.value for option in menu.options[0]] == ["done"]


def test_recruiter_follow_up_keeps_int_friend_id() -> None:
	menu = _ScriptedMenu(["reply"], ["你好"])
	result = _recruiter_follow_up(
		menu,
		{"geekId": "geek-1", "jobId": "job-1", "friendId": 42, "geekName": "候选人"},
		"zhipin",
	)

	assert result is not None
	assert result.goal == "reply"
	assert result.inputs["friend_id"] == 42
	assert result.inputs["message"] == "你好"


def test_recruiter_follow_up_hides_actions_for_container_friend_id() -> None:
	menu = _ScriptedMenu(["done"])

	assert _recruiter_follow_up(menu, {"friendId": {"bad": 1}, "uid": 10**5000}, "zhipin") is None
	assert [option.value for option in menu.options[0]] == ["done"]
