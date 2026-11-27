"""停止已落盘后，断点读不出来也不能把停止报成失败（Issue #1186）。

``request_workflow_stop`` 只改 ``workflow_runs.status``；随后的展示读取会解析
``params`` 等 JSON 列。断点损坏时这条展示路径抛 ``CacheRecordInvalid``，但
停止本身已经生效——控制操作不能因为展示数据读不出来而报 ``ok:false``。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore


def _seed_corrupt_checkpoint(tmp_path):
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_resume",
		inputs={"keyword": "python"},
		requested_steps=("crawl_resume",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-corrupt", plan)
		# 断点本体写坏：展示读取（含 params 解析）读不出来，但停止标记不需要它。
		store._cache._conn.execute("UPDATE workflow_runs SET params = '[]' WHERE run_id = 'run-corrupt'")
		store._cache._conn.commit()


def test_stop_reports_success_when_checkpoint_is_corrupt(tmp_path):
	_seed_corrupt_checkpoint(tmp_path)

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "run-corrupt"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	# 断点读不出来时只退回最小可用展示记录，不能丢掉已生效的停止。
	assert payload["data"]["run_id"] == "run-corrupt"
	assert payload["data"]["status"] == "stopped"
	assert payload["hints"]["next_actions"]
	with WorkflowStore(tmp_path) as store:
		row = store._cache._conn.execute("SELECT status FROM workflow_runs WHERE run_id = 'run-corrupt'").fetchone()
		assert row[0] == "stopped"


def test_status_still_reports_corrupt_checkpoint_after_stop(tmp_path):
	_seed_corrupt_checkpoint(tmp_path)
	CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "run-corrupt"])

	status = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--status", "run-corrupt"])

	assert status.exit_code == 1
	payload = json.loads(status.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"


def _seed_drifted_error_code(tmp_path, code):
	"""真实 run + 仅 error.code 类型漂移：--status 拒绝，--stop 只能降级展示。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_resume",
		inputs={"keyword": "python"},
		requested_steps=("crawl_resume",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-drifted-error", plan)
		drifted = {
			"code": code,
			"message": "drifted",
			"recoverable": True,
			"recovery_action": "核对平台实际状态",
			"replay_safe": False,
		}
		store._cache._conn.execute(
			"UPDATE workflow_runs SET error = ?, status = 'failed' WHERE run_id = 'run-drifted-error'",
			(json.dumps(drifted, ensure_ascii=False),),
		)
		store._cache._conn.commit()


@pytest.mark.parametrize("code", [["ACTION_UNCONFIRMED"], {"nested": "code"}])
def test_stop_degrades_drifted_error_code_instead_of_internal_error(tmp_path, code):
	"""停止已落盘；展示回退里非字符串 error.code 不能触发 unhashable 崩溃（Issue #3072）。"""
	_seed_drifted_error_code(tmp_path, code)

	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "run-drifted-error"]
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["run_id"] == "run-drifted-error"
	assert payload["data"]["status"] == "stopped"
	assert payload["hints"]["next_actions"]
	with WorkflowStore(tmp_path) as store:
		row = store._cache._conn.execute(
			"SELECT status FROM workflow_runs WHERE run_id = 'run-drifted-error'"
		).fetchone()
		assert row[0] == "stopped"


def test_status_still_reports_drifted_error_code_after_stop(tmp_path):
	_seed_drifted_error_code(tmp_path, ["ACTION_UNCONFIRMED"])
	CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "run-drifted-error"]
	)

	status = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--status", "run-drifted-error"]
	)

	assert status.exit_code == 1
	payload = json.loads(status.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
