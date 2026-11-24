"""向导 --resume 校验历史 run 的 inputs 可存储性（Issue #2687）。

新建 plan 在 `WizardInput.from_mapping` 已拒绝 NaN / 孤立代理字符（Issue
#1883、#2058），但 #2058 之前写入的历史 run 可能带着这类值；恢复时动作层
多数参数没有可编码性校验，会在 sqlite 绑参 / httpx 编码阶段落成误导性的
LOCAL_IO_ERROR。本地记录写坏与 params 结构漂移同口径报 CACHE_RECORD_INVALID。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.commands.wizard import _plan_from_run
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore


def _seed_run(tmp_path: Path, run_id: str = "legacy-run") -> None:
	with WorkflowStore(tmp_path) as store:
		store.create(
			run_id,
			WorkflowPlan(
				role="candidate",
				platform="zhipin",
				goal="job_search",
				inputs={"query": "Python"},
				requested_steps=("auth_status", "candidate_search"),
				mode="headless",
			),
		)


def _set_params(tmp_path: Path, value: str, run_id: str = "legacy-run") -> None:
	with sqlite3.connect(tmp_path / "cache" / "boss_agent.db") as conn:
		conn.execute("UPDATE workflow_runs SET params = ? WHERE run_id = ?", (value, run_id))


def _resume(tmp_path: Path, run_id: str = "legacy-run"):
	return CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", run_id]
	)


def test_resume_rejects_surrogate_inputs_with_cache_record_invalid(tmp_path):
	_seed_run(tmp_path)
	# json.dumps 默认 ensure_ascii：孤立代理以 "\ud800" 转义落库，json.loads
	# 读回时又还原成裸代理字符——#2058 之前的写入路径就是这样留下坏记录的。
	_set_params(
		tmp_path,
		json.dumps(
			{
				"inputs": {"query": "\ud800"},
				"requested_steps": ["auth_status", "candidate_search"],
			}
		),
	)

	result = _resume(tmp_path)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "params.inputs" in payload["error"]["message"]
	assert payload["error"]["recoverable"] is True


def test_resume_rejects_nonfinite_inputs(tmp_path):
	_seed_run(tmp_path)
	# JSON 的非标准常量 NaN 被 json.loads 放行；含 NaN 的 plan 一旦恢复，
	# 永远比对不相等，run 事实上不可恢复（Issue #1883）。
	_set_params(
		tmp_path,
		'{"inputs": {"now_ts_ms": NaN}, "requested_steps": ["auth_status", "candidate_search"]}',
	)

	result = _resume(tmp_path)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "params.inputs" in payload["error"]["message"]


def test_plan_from_run_accepts_valid_inputs(tmp_path):
	_seed_run(tmp_path)
	with WorkflowStore(tmp_path) as store:
		run = store.get("legacy-run")

	assert run is not None
	plan = _plan_from_run(run)

	assert plan.inputs == {"query": "Python"}
	assert plan.requested_steps == ("auth_status", "candidate_search")
