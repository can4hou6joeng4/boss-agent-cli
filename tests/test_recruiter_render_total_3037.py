"""招聘者列表 pagination.total 必须等于实际渲染行数（Issue #3037）。

#3034 让四个命令与渲染共用容器键，但两处对「行」的定义仍有差别：命令层把
``null`` 等非对象元素也计为一行，且不接受顶层列表。``total_kind`` 声明为
``returned_page_count``，Agent 只读分页证据就会拿到错误条数。
"""

import json
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands.recruiter._render import (
	candidate_list_rows,
	friend_list_rows,
	job_list_rows,
)
from boss_agent_cli.main import cli

#: 命令 → (模块, CLI 参数, 平台方法, 首选容器, 渲染行函数)。
#: 渲染行函数是终端实际展示所用的公开入口，断言 total 与它同源。
_CASES: list[tuple[str, list[str], str, str, Callable[[Any], list[dict[str, Any]]]]] = [
	("applications", ["hr", "applications"], "friend_list", "friendList", friend_list_rows),
	(
		"recommendations", ["hr", "recommendations", "--job-id", "job_fixture"],
		"recommend_geeks", "geekList", candidate_list_rows,
	),
	("jobs", ["hr", "jobs", "list"], "list_jobs", "jobList", job_list_rows),
	("candidates", ["hr", "candidates", "python"], "search_geeks", "geekList", candidate_list_rows),
]


def _invoke(tmp_path, module: str, args: list[str], method: str, payload: Any) -> dict[str, Any]:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	getattr(platform, method).return_value = {"zpData": payload}
	with patch(f"boss_agent_cli.commands.recruiter.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.recruiter.{module}.get_recruiter_platform_instance",
		return_value=platform,
	):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])
	assert result.exit_code == 0, result.output
	parsed = json.loads(result.stdout)
	assert parsed["ok"] is True
	return parsed


@pytest.mark.parametrize(("module", "args", "method", "container", "rows_of"), _CASES)
def test_total_ignores_non_object_elements(tmp_path, module, args, method, container, rows_of):
	"""容器里混入 ``null`` 等非对象元素时，total 不能把它们计为渲染行。"""
	payload = {container: [{"name": "候选人", "jobName": "岗位"}, None], "hasMore": False}
	parsed = _invoke(tmp_path, module, args, method, payload)
	rendered = rows_of(payload)
	assert len(rendered) == 1
	assert parsed["pagination"]["total"] == len(rendered)
	assert parsed["data"] == payload  # 平台载荷仍原样透传


@pytest.mark.parametrize(("module", "args", "method", "container", "rows_of"), _CASES)
def test_total_counts_top_level_list_rows(tmp_path, module, args, method, container, rows_of):
	"""平台把载荷换成顶层列表时，渲染仍显示行，total 不能是 null。"""
	payload = [{"name": "候选人", "jobName": "岗位"}]
	parsed = _invoke(tmp_path, module, args, method, payload)
	rendered = rows_of(payload)
	assert len(rendered) == 1
	assert parsed["pagination"]["total"] == len(rendered)
	assert parsed["data"] == payload


@pytest.mark.parametrize("container", ["items", "list", "result"])
def test_candidates_total_counts_alternate_containers(tmp_path, container):
	"""``hr candidates`` 的渲染支持替代容器，total 必须同样识别（Issue #3037）。"""
	payload = {container: [{"name": "候选人"}], "hasMore": False}
	parsed = _invoke(tmp_path, "candidates", ["hr", "candidates", "python"], "search_geeks", payload)
	assert parsed["pagination"]["total"] == 1


def test_candidates_total_ignores_non_object_elements_in_alternate_container(tmp_path):
	payload = {"items": [{"name": "候选人"}, "drift"], "hasMore": False}
	parsed = _invoke(tmp_path, "candidates", ["hr", "candidates", "python"], "search_geeks", payload)
	assert parsed["pagination"]["total"] == 1
