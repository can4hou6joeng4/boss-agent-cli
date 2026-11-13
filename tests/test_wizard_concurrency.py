"""同一 workflow 只能有一个执行器，状态查询与停止请求不受执行互斥影响。"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner

from boss_agent_cli.display import shell_argument
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import StepResult, WizardInput, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


def _reply_plan():
	return build_plan(WizardInput.from_mapping({
		"role": "recruiter", "platform": "zhipin", "goal": "reply",
		"inputs": {"friend_id": 42, "message": "offline fixture"},
		"requested_steps": ["recruiter_reply"],
	}))


def _read_plan():
	return WorkflowPlan(
		role="candidate", platform="zhipin", goal="search", inputs={},
		requested_steps=("read",), mode="headless",
	)


def _offline_read(*args):
	return StepResult({"offline": True})


def _status_command(data_dir, run_id):
	return f"boss --data-dir {shell_argument(str(data_dir.resolve()))} wizard --status {run_id}"


def _lock_files(data_dir):
	return list((data_dir / "cache").rglob("*.lock"))


def test_overlapping_resume_does_not_send_reply_or_overwrite_checkpoint(tmp_path):
	"""使用真实回复动作和两个独立连接，竞争者必须在读取执行快照前退出。"""
	entered = threading.Event()
	release = threading.Event()
	sends = []
	events = []
	plan = _reply_plan()

	class OfflineRecruiter:
		def __enter__(self):
			return self

		def __exit__(self, *args):
			pass

		def send_message_by_friend(self, friend_id, message):
			sends.append((friend_id, message))
			if len(sends) == 1:
				entered.set()
				assert release.wait(10)
			return {"code": 0, "data": {"sent": True}}

		def is_success(self, response):
			return response["code"] == 0

		def unwrap_data(self, response):
			return response["data"]

	context = ActionContext(
		tmp_path, "zhipin", "recruiter", Logger("error"),
		recruiter_factory=lambda *args: OfflineRecruiter(),
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-reply", plan)

	def first_worker():
		with WorkflowStore(tmp_path) as store:
			return WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="run-reply")

	with ThreadPoolExecutor(max_workers=1) as executor:
		first = executor.submit(first_worker)
		try:
			assert entered.wait(10)
			with WorkflowStore(tmp_path) as store:
				before = store.get("run-reply")
				with pytest.raises(WorkflowActionError) as rejected:
					WorkflowRunner(store, DEFAULT_ACTIONS).run(
						plan, context, run_id="run-reply",
						on_event=lambda *args: events.append(args),
					)
				assert rejected.value.code == "WORKFLOW_BUSY"
				assert rejected.value.recoverable is True
				assert rejected.value.details == {"run_id": "run-reply"}
				assert _status_command(tmp_path, "run-reply") in rejected.value.recovery_action
				assert store.get("run-reply") == before
				assert events == []
		finally:
			release.set()
		result = first.result(timeout=10)

	assert result["status"] == "completed"
	assert len(sends) == 1
	with WorkflowStore(tmp_path) as store:
		# 持锁者完成之后恢复应读取已保存的成功结果，不再发送。
		resumed = WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="run-reply")
	assert resumed["steps"][0]["result"] == result["steps"][0]["result"]
	assert len(sends) == 1


def test_headless_resume_reports_busy_with_status_as_the_only_next_action(tmp_path):
	with WorkflowStore(tmp_path) as store:
		store.create("run-headless", _reply_plan())
		with store.execution_lock("run-headless") as acquired:
			assert acquired
			before = store.get("run-headless")
			result = CliRunner().invoke(
				cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "run-headless"],
			)
			assert store.get("run-headless") == before

	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert set(payload) == ENVELOPE_KEYS
	assert payload["ok"] is False
	assert payload["error"]["code"] == "WORKFLOW_BUSY"
	assert payload["error"]["recoverable"] is True
	assert payload["error"]["details"] == {"run_id": "run-headless"}
	status_command = _status_command(tmp_path, "run-headless")
	assert status_command in payload["error"]["recovery_action"]
	# 照做 --resume 只会再撞一次，照做 --stop 会打断一次正常的执行。
	assert payload["hints"]["next_actions"] == [status_command]


def test_tty_resume_explains_busy_without_touching_stdout(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	with WorkflowStore(tmp_path) as store:
		store.create("run-tty", _reply_plan())
		with store.execution_lock("run-tty") as acquired:
			assert acquired
			result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard", "--resume", "run-tty"])

	assert result.exit_code == 1
	assert result.stdout == ""
	assert "另一次调用执行" in result.stderr
	assert "查看任务状态" in result.stderr
	assert "请返回主菜单后重试" not in result.stderr


def _locking_unsupported(data_dir, monkeypatch):
	def unsupported(handle):
		raise OSError(errno.ENOLCK, "No locks available")

	monkeypatch.setattr("boss_agent_cli.services.file_lock.try_lock_exclusive", unsupported)


def _lock_directory_blocked(data_dir, monkeypatch):
	(data_dir / "cache" / "workflow-locks").write_text("not a directory", encoding="utf-8")


@pytest.mark.parametrize(
	"break_locking",
	[_locking_unsupported, _lock_directory_blocked],
	ids=["locking-unsupported", "lock-directory-blocked"],
)
def test_unavailable_lock_fails_closed_as_a_local_file_error(tmp_path, monkeypatch, break_locking):
	"""加不了锁（文件系统不支持文件锁、锁目录不可用）不能退化成无锁执行。"""
	calls = []
	monkeypatch.setitem(
		DEFAULT_ACTIONS, "recruiter_reply", lambda *args: calls.append(args) or StepResult({"offline": True}),
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-nolock", _reply_plan())
		before = store.get("run-nolock")
		break_locking(tmp_path, monkeypatch)
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "run-nolock"])
		status = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--status", "run-nolock"])
		assert store.get("run-nolock") == before

	assert calls == []
	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert set(payload) == ENVELOPE_KEYS
	# 不是 NETWORK_ERROR：对一个确定性的本地问题给出「重试」，调用方只会循环。
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
	# 查看状态不取执行权，锁出问题时仍然可用。
	assert status.exit_code == 0
	assert json.loads(status.stdout)["data"]["run_id"] == "run-nolock"


_CLI_SCRIPT = textwrap.dedent("""\
from boss_agent_cli.commands.wizard import DEFAULT_ACTIONS
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import StepResult
DEFAULT_ACTIONS["recruiter_reply"] = lambda *args: StepResult({"offline": True})
cli()
""")


def _run_offline_cli(data_dir, *wizard_args, global_args=()):
	return subprocess.run(
		[sys.executable, "-c", _CLI_SCRIPT, "--data-dir", str(data_dir), "--json", *global_args, "wizard", *wizard_args],
		capture_output=True, text=True, timeout=30,
	)


def test_other_process_gets_busy_envelope_without_modifying_run(tmp_path):
	with WorkflowStore(tmp_path) as store:
		store.create("run-process", _reply_plan())
		with store.execution_lock("run-process") as acquired:
			assert acquired
			before = store.get("run-process")

			result = _run_offline_cli(tmp_path, "--resume", "run-process")
			assert result.returncode == 1, result.stderr
			payload = json.loads(result.stdout)
			assert set(payload) == ENVELOPE_KEYS
			assert payload["error"]["code"] == "WORKFLOW_BUSY"
			assert payload["error"]["details"] == {"run_id": "run-process"}
			assert payload["hints"]["next_actions"] == [_status_command(tmp_path, "run-process")]
			assert store.get("run-process") == before

			# 「稍后再来」在 sysexits 下是临时失败，脚本可以据此退避。
			busy = _run_offline_cli(tmp_path, "--resume", "run-process", global_args=("--exit-codes", "sysexits"))
			assert busy.returncode == 75, busy.stderr
			assert json.loads(busy.stdout)["error"]["code"] == "WORKFLOW_BUSY"

			# 状态查询不取执行权：别的进程执行期间照常返回。
			status = _run_offline_cli(tmp_path, "--status", "run-process")
			assert status.returncode == 0, status.stderr
			assert json.loads(status.stdout)["data"]["run_id"] == "run-process"
			assert store.get("run-process") == before

	# 释放后，另一进程可继续原 run；动作已换成纯离线替身。
	resumed = _run_offline_cli(tmp_path, "--resume", "run-process")
	assert resumed.returncode == 0, resumed.stderr
	assert json.loads(resumed.stdout)["data"]["status"] == "completed"
	assert _lock_files(tmp_path) == []


def test_status_and_stop_remain_available_during_execution(tmp_path):
	entered = threading.Event()
	release = threading.Event()

	def read(context, inputs, prior):
		entered.set()
		assert release.wait(10)
		return StepResult({"offline": True})

	def worker():
		with WorkflowStore(tmp_path) as store:
			return WorkflowRunner(store, {"read": read}).run(_read_plan(), object(), run_id="run-stop")

	with ThreadPoolExecutor(max_workers=1) as executor:
		first = executor.submit(worker)
		try:
			assert entered.wait(10)
			with WorkflowStore(tmp_path) as store:
				assert store.get("run-stop")["status"] == "running"
				assert store.request_stop("run-stop")
				assert store.get("run-stop")["status"] == "stopped"
		finally:
			release.set()
		result = first.result(timeout=10)
	assert result["status"] == "stopped"


@pytest.mark.parametrize(
	("raised", "outcome"),
	[
		(WorkflowActionError("RATE_LIMITED", "offline failure", recoverable=True), "failed"),
		# runner 把中断记成 stopped 并正常返回。
		(KeyboardInterrupt(), "stopped"),
		# runner 不接管的异常直接向外抛，执行权同样要交还。
		(SystemExit(3), None),
	],
	ids=["controlled-failure", "interrupt", "escaping-exception"],
)
def test_execution_right_is_returned_however_the_run_ends(tmp_path, raised, outcome):
	def fail(context, inputs, prior):
		raise raised

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"read": fail})
		if outcome is None:
			with pytest.raises(type(raised)):
				runner.run(_read_plan(), object(), run_id="run-ended")
		else:
			assert runner.run(_read_plan(), object(), run_id="run-ended")["status"] == outcome
		with store.execution_lock("run-ended") as acquired:
			assert acquired
	assert _lock_files(tmp_path) == []


def test_execution_is_released_when_process_exits_abruptly(tmp_path):
	"""进程未执行 finally 就退出时，纯读取步骤仍可恢复，锁文件无须手工删除。"""
	with WorkflowStore(tmp_path) as store:
		store.create("run-exit", _read_plan())
	child = subprocess.run(
		[sys.executable, "-c", textwrap.dedent("""\
import os
import sys
from pathlib import Path
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore
plan = WorkflowPlan(role="candidate", platform="zhipin", goal="search", inputs={}, requested_steps=("read",), mode="headless")
with WorkflowStore(Path(sys.argv[1])) as store:
    WorkflowRunner(store, {"read": lambda *args: os._exit(17)}).run(plan, object(), run_id="run-exit")
"""), str(tmp_path)],
		capture_output=True, text=True, timeout=30,
	)
	assert child.returncode == 17, child.stderr
	# 崩溃的进程来不及删除锁文件；它不代表占用，下一次执行直接接手并清掉。
	assert len(_lock_files(tmp_path)) == 1
	with WorkflowStore(tmp_path) as store:
		assert store.get("run-exit")["status"] == "running"
		resumed = WorkflowRunner(store, {"read": _offline_read}).run(_read_plan(), object(), run_id="run-exit")
	assert resumed["status"] == "completed"
	assert _lock_files(tmp_path) == []


def test_different_runs_can_execute_independently(tmp_path):
	with WorkflowStore(tmp_path) as store:
		with store.execution_lock("another-run") as acquired:
			assert acquired
			run = WorkflowRunner(store, {"read": _offline_read}).run(_read_plan(), object(), run_id="run-independent")
			# 未指定 run_id 的新任务同样不受其他 run 影响。
			fresh = WorkflowRunner(store, {"read": _offline_read}).run(_read_plan(), object())
	assert run["status"] == "completed"
	assert fresh["status"] == "completed"
	assert _lock_files(tmp_path) == []


@pytest.mark.parametrize(
	"run_id",
	["../../outside", "nested/run", "run with spaces", "任务-1", "a" * 400],
	ids=["traversal", "separator", "spaces", "non-ascii", "long"],
)
def test_execution_lock_file_name_is_independent_of_the_run_id(tmp_path, run_id):
	"""run_id 是调用方给的任意字符串，不能变成路径的一部分。"""
	with WorkflowStore(tmp_path) as store:
		with store.execution_lock(run_id) as acquired:
			assert acquired
			assert [path.parent for path in tmp_path.rglob("*.lock")] == [tmp_path / "cache" / "workflow-locks"]
			with store.execution_lock(run_id) as again:
				assert not again
	assert list(tmp_path.rglob("*.lock")) == []
