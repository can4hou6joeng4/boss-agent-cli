"""向导枚举型输入不再用真值判断吞掉非字符串显式值（Issue #2701）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.models import WorkflowInputError


def _context(tmp_path: Path) -> ActionContext:
	def factory(name: str, auth: Any, delay: Any, cdp_url: Any, browser_source: Any = None) -> Any:
		raise AssertionError("不应构造平台实例")

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
		recruiter_factory=factory,
	)


BAD_VALUES = [False, 0, [], {}, ["run"], 1.5, True]


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_watch_action_rejects_non_string(tmp_path: Path, raw: Any) -> None:
	with pytest.raises(WorkflowInputError, match="action"):
		DEFAULT_ACTIONS["candidate_watch"](_context(tmp_path), {"action": raw, "name": "daily"}, {})


@pytest.mark.parametrize("inputs", [{}, {"action": None}, {"action": ""}, {"action": "list"}])
def test_watch_action_default_is_list(tmp_path: Path, inputs: dict[str, Any]) -> None:
	result = DEFAULT_ACTIONS["candidate_watch"](_context(tmp_path), inputs, {})
	assert result.data == {"items": [], "total": 0}


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_export_format_rejects_non_string(tmp_path: Path, raw: Any) -> None:
	with pytest.raises(WorkflowInputError, match="format"):
		DEFAULT_ACTIONS["candidate_export"](_context(tmp_path), {"query": "Python", "format": raw}, {})
	assert not (tmp_path / "exports").exists()


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_crawl_hook_profile_rejects_non_string(tmp_path: Path, raw: Any) -> None:
	with pytest.raises(WorkflowInputError, match="hook_profile"):
		DEFAULT_ACTIONS["crawl_start"](
			_context(tmp_path), {"query": "Python", "city": "上海", "hook_profile": raw}, {}
		)
	assert not (tmp_path / "crawl").exists()
