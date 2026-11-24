"""向导 search / export 的 page、max_pages 在构造平台前校验（Issue #2692）。

两个 action 此前先 `with context.candidate_platform()` 再进入
`execute_candidate_search`，页码 / 页数预算在后者内部才求值：非法值会先付出
一次平台实例构造（CDP 模式会启动浏览器或做登录探测）才得到 INVALID_PARAM。
同文件的 `_candidate_recommend` / `_candidate_chat` 都已在平台前校验（#2625）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError


class _Platform:
	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None


class _Result:
	items: list[dict[str, Any]] = []
	has_more_reported: bool | None = False
	total = 0


def _context(tmp_path: Path) -> ActionContext:
	def factory(*args: object, **kwargs: object) -> _Platform:
		raise AssertionError("非法输入不应构造平台实例")

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


@pytest.mark.parametrize(
	("action", "inputs"),
	[
		("candidate_search", {"query": "Python", "page": 0}),
		("candidate_search", {"query": "Python", "page": "abc"}),
		("candidate_search", {"query": "Python", "page": 1_000_001}),
		("candidate_search", {"query": "Python", "max_pages": 0}),
		("candidate_search", {"query": "Python", "max_pages": "5x"}),
		("candidate_export", {"query": "Python", "page": 0}),
		("candidate_export", {"query": "Python", "page": "abc"}),
		("candidate_export", {"query": "Python", "max_pages": 0}),
	],
)
def test_invalid_page_budget_rejected_before_platform(tmp_path, action, inputs):
	# `_context` 的 factory 一旦被调用就抛 AssertionError：测试通过即说明平台
	# 实例没有被构造，#2625 的「越界值不构造平台」口径在 search / export 生效。
	with pytest.raises(WorkflowInputError, match="page"):
		DEFAULT_ACTIONS[action](_context(tmp_path), inputs, {})


def test_valid_budget_still_reaches_platform(tmp_path, monkeypatch):
	captured: dict[str, Any] = {}

	def fake(platform: Any, cache: Any, logger: Any, inputs: dict[str, Any], **kwargs: Any) -> _Result:
		captured["inputs"] = dict(inputs)
		return _Result()

	monkeypatch.setattr("boss_agent_cli.wizard.actions.execute_candidate_search", fake)

	def factory(*args: object, **kwargs: object) -> _Platform:
		return _Platform()

	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)

	data = DEFAULT_ACTIONS["candidate_search"](
		context, {"query": "Python", "page": 3, "max_pages": 2}, {}
	).data

	assert captured["inputs"]["page"] == 3
	assert captured["inputs"]["max_pages"] == 2
	# 回显的页码与预校验读过的是同一个值。
	assert data["pagination"]["page"] == 3
