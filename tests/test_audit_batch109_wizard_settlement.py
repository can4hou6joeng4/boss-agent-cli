"""批次 109：wizard 结算语义与断点读取边界（Issue #2441–#2443）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard import actions
from boss_agent_cli.wizard.actions import ActionContext
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowControl
from boss_agent_cli.wizard.store import WorkflowStore


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan("candidate", "zhipin", "job_search", {}, steps, "headless")


class _Platform:
	"""假平台上下文：只实现本批次动作会调到的方法。"""

	def __init__(self, error: BaseException) -> None:
		self._error = error

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> bool:
		return False

	def exchange_contact(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		raise self._error

	def friend_label(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		raise self._error

	def job_online(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		raise self._error

	def job_offline(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		raise self._error

	def view_geek(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		raise self._error


_FRIEND = {"uid": 11, "securityId": "sid_1", "name": "甲", "friendSource": 0}


def _candidate_context(tmp_path: Path, control: MagicMock, error: BaseException) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger("error"),
		workflow_control=control,
		candidate_factory=lambda *args, **kwargs: _Platform(error),
	)


def _recruiter_context(tmp_path: Path, control: MagicMock, error: BaseException) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		workflow_control=control,
		recruiter_factory=lambda *args, **kwargs: _Platform(error),
	)


# ── Issue #2441：单请求写动作的派发前失败撤销写标记 ─────────────────


def test_exchange_pre_dispatch_failure_clears_write_marker(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	control = MagicMock()
	context = _candidate_context(tmp_path, control, ProxyConfigurationError("HTTPS_PROXY"))
	monkeypatch.setattr(actions, "_candidate_friend", lambda platform, identifier, context=None: dict(_FRIEND))

	with pytest.raises(ProxyConfigurationError):
		actions._candidate_exchange(context, {"security_id": "sid_1"}, {})

	control.begin_write.assert_called_once()
	control.clear_write.assert_called_once()


def test_exchange_post_dispatch_failure_keeps_write_marker(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	control = MagicMock()
	context = _candidate_context(tmp_path, control, RuntimeError("connection reset after dispatch"))
	monkeypatch.setattr(actions, "_candidate_friend", lambda platform, identifier, context=None: dict(_FRIEND))

	with pytest.raises(RuntimeError):
		actions._candidate_exchange(context, {"security_id": "sid_1"}, {})

	control.begin_write.assert_called_once()
	control.clear_write.assert_not_called()


def test_mark_pre_dispatch_failure_clears_write_marker(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	control = MagicMock()
	context = _candidate_context(tmp_path, control, ProxyConfigurationError("HTTPS_PROXY"))
	monkeypatch.setattr(actions, "_candidate_friend", lambda platform, identifier, context=None: dict(_FRIEND))

	with pytest.raises(ProxyConfigurationError):
		actions._candidate_mark(context, {"security_id": "sid_1", "label": "1"}, {})

	control.clear_write.assert_called_once()


def test_mark_post_dispatch_failure_keeps_write_marker(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	control = MagicMock()
	context = _candidate_context(tmp_path, control, RuntimeError("connection reset after dispatch"))
	monkeypatch.setattr(actions, "_candidate_friend", lambda platform, identifier, context=None: dict(_FRIEND))

	with pytest.raises(RuntimeError):
		actions._candidate_mark(context, {"security_id": "sid_1", "label": "1"}, {})

	control.clear_write.assert_not_called()


@pytest.mark.parametrize("action_name", ["recruiter_jobs_online", "recruiter_jobs_offline"])
def test_recruiter_job_state_pre_dispatch_failure_clears_write_marker(
	tmp_path: Path, action_name: str
) -> None:
	control = MagicMock()
	context = _recruiter_context(tmp_path, control, ProxyConfigurationError("HTTPS_PROXY"))

	with pytest.raises(ProxyConfigurationError):
		actions.DEFAULT_ACTIONS[action_name](context, {"job_id": "j1"}, {})

	control.clear_write.assert_called_once()


@pytest.mark.parametrize("action_name", ["recruiter_jobs_online", "recruiter_jobs_offline"])
def test_recruiter_job_state_post_dispatch_failure_keeps_write_marker(
	tmp_path: Path, action_name: str
) -> None:
	control = MagicMock()
	context = _recruiter_context(tmp_path, control, RuntimeError("connection reset after dispatch"))

	with pytest.raises(RuntimeError):
		actions.DEFAULT_ACTIONS[action_name](context, {"job_id": "j1"}, {})

	control.clear_write.assert_not_called()


def test_recruiter_resume_stays_fail_closed_without_provably_unsent(
	tmp_path: Path,
) -> None:
	"""view_geek 是复合动作（effect: unknown），派发前异常也不得撤销写标记。"""
	control = MagicMock()
	context = _recruiter_context(tmp_path, control, ProxyConfigurationError("HTTPS_PROXY"))

	with pytest.raises(ProxyConfigurationError):
		actions._recruiter_resume(context, {"geek_id": "g1", "job_id": "j1"}, {})

	control.clear_write.assert_not_called()


# ── Issue #2442：failed/stopped 步骤带 completed 结果同样拒绝 ───────


@pytest.mark.parametrize("status", ["pending", "running", "waiting_input", "failed", "stopped"])
def test_non_completed_step_with_completed_result_is_rejected(tmp_path: Path, status: str) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("step"))
		store.update_step("r", "step", status=status, result=StepResult({}).to_dict())

		with pytest.raises(CacheRecordInvalid, match=f"步骤状态是 {status}"):
			store.get_checkpoint("r")


def test_completed_step_with_completed_result_is_accepted(tmp_path: Path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("step"))
		store.update_step("r", "step", status="completed", result=StepResult({}).to_dict())

		assert store.get_checkpoint("r")["run_id"] == "r"


# ── Issue #2443：控制轮询只读原始状态列 ────────────────────────────


def _drifted_stopped_store(tmp_path: Path) -> WorkflowStore:
	store = WorkflowStore(tmp_path)
	store.create("r", _plan("step"))
	store.update_step(
		"r",
		"step",
		status="running",
		error={"code": "ACTION_UNCONFIRMED", "replay_safe": "false"},
	)
	store.update_run("r", status="stopped", current_step="step")
	return store


def test_poll_returns_stop_despite_drifted_replay_marker(tmp_path: Path) -> None:
	with _drifted_stopped_store(tmp_path) as store:
		# 完整断点读取仍按记录损坏拒绝（读取边界不变）。
		with pytest.raises(CacheRecordInvalid):
			store.get_checkpoint("r")
		# 控制轮询只依赖原始状态列，停止信号不被漂移字段吞掉。
		assert WorkflowControl("r", "step", store).poll() == "stop"


def test_raise_if_requested_survives_drifted_checkpoint(tmp_path: Path) -> None:
	with _drifted_stopped_store(tmp_path) as store:
		with pytest.raises(WorkflowActionError) as excinfo:
			WorkflowControl("r", "step", store).raise_if_requested()

		assert excinfo.value.code == "WORKFLOW_STOPPED"


def test_poll_returns_timeout_after_deadline(tmp_path: Path) -> None:
	with WorkflowStore(tmp_path) as store:
		store.create("r", _plan("step"))
		control = WorkflowControl("r", "step", store, deadline=time.monotonic() - 1)

		assert control.poll() == "timeout"


def test_poll_with_missing_run_returns_none(tmp_path: Path) -> None:
	with WorkflowStore(tmp_path) as store:
		assert WorkflowControl("missing", "step", store).poll() is None
