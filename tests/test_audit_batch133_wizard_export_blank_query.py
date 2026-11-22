"""审计批次 133：向导 export 动作拒绝空白关键词（Issue #2519）。

`export` goal 只要求 ``query`` 键存在，``_candidate_export`` 之前未校验内容，
``execute_candidate_search`` 里的 ``or ""`` 会把纯空白关键词当成无关键词搜索，
导出文件照常生成并报成功。CLI `boss export " "` 与 `candidate_search` 都拒绝。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


class _FakeCandidatePlatform:
	def __enter__(self) -> "_FakeCandidatePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


def _forbidden_factory(*args: object, **kwargs: object) -> object:
	raise AssertionError("非法输入不应构造平台实例")


def _context(tmp_path: Path, *, platform: object | None = None) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=(lambda *args, **kwargs: platform) if platform is not None else _forbidden_factory,
	)


def test_export_rejects_blank_query_before_platform_request(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path),
			{"query": "   ", "output": str(tmp_path / "out.json")},
			{},
		)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "query" in str(exc_info.value)


def test_export_accepts_nonblank_query(tmp_path: Path) -> None:
	class _Result:
		items: list[object] = []

	with patch("boss_agent_cli.wizard.actions.execute_candidate_search", return_value=_Result()):
		result = DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path, platform=_FakeCandidatePlatform()),
			{"query": "golang", "output": str(tmp_path / "out.json")},
			{},
		)

	assert result.data["count"] == 0
	assert Path(result.data["path"]) == tmp_path / "out.json"
