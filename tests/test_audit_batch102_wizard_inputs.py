"""第二十二轮审计批次：断点非终态一致性、ID 字段与 wizard 输入边界（Issue #2418–#2421）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.wizard import actions
from boss_agent_cli.wizard.models import (
	StepResult,
	WorkflowInputError,
	WorkflowPlan,
	_ensure_storable_inputs,
)
from boss_agent_cli.wizard.runner import WorkflowActionError
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan("candidate", "zhipin", "job_search", {}, steps, "headless")


# ── Issue #2418：非终态步骤带 completed 结果一律拒绝 ──────────────


@pytest.mark.parametrize("status", ["pending", "running", "waiting_input"])
def test_non_terminal_step_with_completed_result_is_rejected(tmp_path: Path, status: str) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("step"))
		store.update_step("r", "step", status=status, result=StepResult({}).to_dict())

		with pytest.raises(CacheRecordInvalid, match=f"步骤状态是 {status}"):
			store.get_checkpoint("r")


@pytest.mark.parametrize("status", ["failed", "stopped"])
def test_terminal_step_with_completed_result_is_rejected(tmp_path: Path, status: str) -> None:
	# failed / stopped 步骤带完成结果时恢复循环会把它重跑一次（Issue #2442）；
	# 当前执行器不会写出这种组合，读取边界按记录损坏拒绝。
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("step"))
		store.update_step("r", "step", status=status, result=StepResult({}).to_dict())

		with pytest.raises(CacheRecordInvalid, match=f"步骤状态是 {status}"):
			store.get_checkpoint("r")


# ── Issue #2419：ID 字段拒绝容器，不能转 repr 发给平台 ─────────────


@pytest.mark.parametrize(
	"action_name",
	["recruiter_jobs_detail", "recruiter_jobs_online", "recruiter_jobs_offline"],
)
def test_recruiter_jobs_reject_container_job_id(action_name: str) -> None:
	action = actions.DEFAULT_ACTIONS[action_name]

	with pytest.raises(WorkflowInputError, match="job_id 必须是字符串"):
		action(None, {"job_id": {"x": 1}}, {})  # type: ignore[arg-type]


def test_recruiter_resume_rejects_container_ids() -> None:
	action = actions.DEFAULT_ACTIONS["recruiter_resume"]

	with pytest.raises(WorkflowInputError, match="geek_id 必须是字符串"):
		action(None, {"geek_id": ["g"], "job_id": "j"}, {})  # type: ignore[arg-type]


# ── Issue #2420：不可 JSON 序列化的输入在入口拒绝 ──────────────────


@pytest.mark.parametrize("value", [b"raw", {1, 2}, object()])
def test_unstorable_inputs_are_rejected(value: object) -> None:
	with pytest.raises(WorkflowInputError, match="不是可存储为 JSON 的值"):
		_ensure_storable_inputs({"marker": value})


def test_unstorable_mapping_key_is_rejected() -> None:
	with pytest.raises(WorkflowInputError, match="的键类型 tuple 不能落成 JSON"):
		_ensure_storable_inputs({("a", 1): "x"})


def test_storable_scalars_and_containers_still_pass() -> None:
	_ensure_storable_inputs(
		{"a": None, "b": True, "c": 3, "d": 1.5, "e": "x", "f": [1, "y"], "g": {"h": 2}}
	)


# ── Issue #2421：welfare 数组元素必须是字符串 ─────────────────────


def test_watch_rejects_non_string_welfare_items(tmp_path: Path) -> None:
	(tmp_path / "cache").mkdir()
	context = SimpleNamespace(data_dir=tmp_path)

	with pytest.raises(WorkflowActionError) as raised:
		actions._candidate_watch(  # type: ignore[arg-type]
			context,
			{"action": "add", "name": "w", "query": "python", "welfare": [{"a": 1}, "双休"]},
			{},
		)

	assert raised.value.code == "INVALID_PARAM"
	assert "welfare 的第 0 个元素" in str(raised.value)


def test_watch_still_joins_string_welfare_items(tmp_path: Path) -> None:
	(tmp_path / "cache").mkdir()
	context = SimpleNamespace(data_dir=tmp_path)

	result = actions._candidate_watch(  # type: ignore[arg-type]
		context,
		{"action": "add", "name": "w", "query": "python", "welfare": ["双休", " 五险一金 "]},
		{},
	)

	assert result.status.value == "completed"
