"""释放预约失败后的错误语义与可执行清理入口（Issues #2971 #2972）。

请求可证明未发出（预算停止 / 认证 / 代理等派发前失败）且本地预约释放失败时，
照原错误的恢复动作重试会撞上残留 pending 并落成 ``ACTION_UNCONFIRMED``。四个
入口必须统一报不可重试的 ``LOCAL_STATE_WRITE_FAILED``，恢复指引指向
``clean --pending-claims <reference>``；只删除指定版本的预约，保留其他 pending 与 sent 行。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.candidate_actions import CandidateActionReleaseFailed, run_candidate_action
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError

CLEAN_HINT = "clean --pending-claims"


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


def _claim_state(data_dir: Path, job_id: str, action: str = "greet") -> str | None:
	connection = sqlite3.connect(data_dir / "cache" / "boss_agent.db")
	try:
		row = connection.execute(
			"SELECT status FROM candidate_action_claims WHERE job_id = ? AND action = ?",
			(job_id, action),
		).fetchone()
	finally:
		connection.close()
	return str(row[0]) if row is not None else None


def _cli_platform() -> MagicMock:
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.greet.side_effect = RequestBudgetExceeded("fixture budget stop")
	platform.apply.side_effect = RequestBudgetExceeded("fixture budget stop")
	return platform


def test_unsent_exception_release_failure_becomes_release_failed(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		with patch.object(
			cache,
			"release_candidate_action",
			side_effect=sqlite3.OperationalError("database is locked"),
		):
			with pytest.raises(CandidateActionReleaseFailed) as caught:
				run_candidate_action(
					cache,
					security_id="sec-1",
					job_id="job-1",
					action="greet",
					send=lambda: (_ for _ in ()).throw(RequestBudgetExceeded("fixture budget stop")),
				)

	assert isinstance(caught.value.cause, sqlite3.OperationalError)
	assert _claim_state(tmp_path, "job-1") == "pending"


def test_cli_greet_budget_release_failure_points_to_clean(tmp_path: Path) -> None:
	platform = _cli_platform()
	with (
		patch("boss_agent_cli.commands.greet.AuthManager"),
		patch("boss_agent_cli.commands.greet.get_platform_instance") as factory,
		patch(
			"boss_agent_cli.cache.store.CacheStore.release_candidate_action",
			side_effect=sqlite3.OperationalError("database is locked"),
		),
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		result = _invoke(tmp_path, "greet", "sec-1", "job-1")

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert CLEAN_HINT in error["recovery_action"]
	assert error["details"]["request_sent"] is False
	assert error["details"]["release_error"] == "OperationalError"
	assert _claim_state(tmp_path, "job-1") == "pending"


def test_cli_apply_budget_release_failure_points_to_clean(tmp_path: Path) -> None:
	platform = _cli_platform()
	with (
		patch("boss_agent_cli.commands.apply.AuthManager"),
		patch("boss_agent_cli.commands.apply.get_platform_instance") as factory,
		patch(
			"boss_agent_cli.cache.store.CacheStore.release_candidate_action",
			side_effect=sqlite3.OperationalError("database is locked"),
		),
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		result = _invoke(tmp_path, "apply", "sec-1", "job-1")

	assert result.exit_code == 1, result.output
	error = json.loads(result.stdout)["error"]
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert CLEAN_HINT in error["recovery_action"]
	assert error["details"]["request_sent"] is False
	assert _claim_state(tmp_path, "job-1", "apply") == "pending"


def _wizard_release_failure(tmp_path: Path, action: str) -> Any:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	getattr(platform, action).side_effect = RequestBudgetExceeded("fixture budget stop")
	control = MagicMock()
	cache = MagicMock()
	cache.__enter__.return_value = cache
	cache.is_applied.return_value = False
	cache.is_greeted.return_value = False
	cache.claim_candidate_action.return_value = None
	cache.release_candidate_action.side_effect = sqlite3.OperationalError("database is locked")
	cache.candidate_action_release_reference.return_value = "a" * 32
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args: platform,
		workflow_control=control,
	)
	with (
		patch.object(ActionContext, "auth", return_value=MagicMock()),
		patch("boss_agent_cli.wizard.actions.account_scope", return_value="demo-account"),
		patch("boss_agent_cli.wizard.actions.CacheStore", return_value=cache),
		patch("boss_agent_cli.wizard.actions.AutomationStore.stop_reason_for", return_value=None),
	):
		with pytest.raises(WorkflowActionError) as caught:
			DEFAULT_ACTIONS[f"candidate_{action}"](
				context, {"security_id": "demo-sec", "job_id": "job-1"}, {}
			)
	assert control.clear_write.called
	return caught.value


@pytest.mark.parametrize("action", ["greet", "apply"])
def test_wizard_budget_release_failure_is_local_state_error(tmp_path: Path, action: str) -> None:
	error = _wizard_release_failure(tmp_path, action)

	assert error.code == "LOCAL_STATE_WRITE_FAILED"
	assert error.recoverable is False
	assert error.replay_safe is False
	assert CLEAN_HINT in error.recovery_action
	assert error.details is not None
	assert error.details["request_sent"] is False


def _seed_claims(data_dir: Path) -> str:
	db = data_dir / "cache" / "boss_agent.db"
	db.parent.mkdir(parents=True)
	with CacheStore(db) as cache:
		cache.claim_candidate_action("sec-pending", "job-pending", "greet")
		cache.mark_candidate_action_sent("sec-sent", "job-sent", "greet")
		cache.claim_candidate_action("sec-other", "job-other", "greet")
		reference = cache.candidate_action_release_reference("job-pending", "greet")
		assert reference is not None
		return reference


def _claims_entry(data: dict[str, Any]) -> dict[str, Any]:
	return next(row for row in data["results"] if row["target"] == "未完成的动作预约")


def test_clean_pending_claims_dry_run_reports_without_deleting(tmp_path: Path) -> None:
	reference = _seed_claims(tmp_path)

	result = _invoke(tmp_path, "clean", "--pending-claims", reference, "--dry-run")

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert data["dry_run"] is True
	assert _claims_entry(data)["cleaned"] == 1
	assert _claim_state(tmp_path, "job-pending") == "pending"


def test_clean_pending_claims_deletes_only_pending_rows(tmp_path: Path) -> None:
	reference = _seed_claims(tmp_path)

	result = _invoke(tmp_path, "clean", "--pending-claims", reference)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert _claims_entry(data)["cleaned"] == 1
	assert _claim_state(tmp_path, "job-pending") is None
	assert _claim_state(tmp_path, "job-sent") == "sent"



def test_clean_reference_does_not_release_other_actions_or_accounts(tmp_path: Path) -> None:
	reference = _seed_claims(tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.claim_candidate_action("sec-pending", "job-pending", "apply")
		cache.claim_candidate_action("sec-pending", "job-pending", "greet", account="other-account")

	result = _invoke(tmp_path, "clean", "--pending-claims", reference)

	assert result.exit_code == 0, result.output
	assert _claims_entry(json.loads(result.stdout)["data"])["cleaned"] == 1
	assert _claim_state(tmp_path, "job-other") == "pending"
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.claim_candidate_action("sec-pending", "job-pending", "apply") == "pending"
		assert cache.claim_candidate_action("sec-pending", "job-pending", "greet", account="other-account") == "pending"


def test_old_recovery_reference_cannot_clear_new_claim(tmp_path: Path) -> None:
	reference = _seed_claims(tmp_path)
	assert _invoke(tmp_path, "clean", "--pending-claims", reference).exit_code == 0
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.claim_candidate_action("sec-pending", "job-pending", "greet") is None

	result = _invoke(tmp_path, "clean", "--pending-claims", reference)

	assert result.exit_code == 0, result.output
	assert _claims_entry(json.loads(result.stdout)["data"])["cleaned"] == 0
	assert _claim_state(tmp_path, "job-pending") == "pending"


def test_preview_hint_retains_reference_and_data_directory(tmp_path: Path) -> None:
	import shlex

	reference = _seed_claims(tmp_path)
	preview = _invoke(tmp_path, "clean", "--pending-claims", reference, "--dry-run")
	assert preview.exit_code == 0, preview.output
	action = json.loads(preview.stdout)["hints"]["next_actions"][0]
	argv = shlex.split(action)
	assert argv[argv.index("--pending-claims") + 1] == reference
	assert argv[argv.index("--data-dir") + 1] == str(tmp_path.resolve())
	assert "--dry-run" not in argv
	actual = CliRunner().invoke(cli, ["--json", *argv[1:]])
	assert actual.exit_code == 0, actual.output
	assert _claim_state(tmp_path, "job-pending") is None
	assert _claim_state(tmp_path, "job-other") == "pending"


@pytest.mark.parametrize("reference", ["", "*", "all", "a" * 31, "a" * 33])
def test_invalid_recovery_reference_does_not_clean(tmp_path: Path, reference: str) -> None:
	_seed_claims(tmp_path)
	result = _invoke(tmp_path, "clean", "--pending-claims", reference)
	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "INVALID_PARAM"
	assert _claim_state(tmp_path, "job-pending") == "pending"


def test_unreadable_reference_does_not_suggest_unscoped_cleanup(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		with (
			patch.object(cache, "release_candidate_action", side_effect=sqlite3.OperationalError("disk full")),
			patch.object(cache, "candidate_action_release_reference", side_effect=sqlite3.OperationalError("unreadable")),
		):
			with pytest.raises(CandidateActionReleaseFailed) as caught:
				run_candidate_action(
					cache, security_id="S", job_id="J", action="greet",
					send=lambda: (_ for _ in ()).throw(RequestBudgetExceeded("stop")),
				)
	assert caught.value.claim_reference is None
	from boss_agent_cli.services.candidate_actions import candidate_release_recovery_action
	recovery = candidate_release_recovery_action(None, lambda command: command)
	assert "clean --pending-claims" not in recovery
	assert "勿删除整库或直接重发" in recovery


def test_mcp_clean_forwards_reference_and_rejects_boolean() -> None:
	from boss_agent_cli.mcp_args import _build_args
	from boss_agent_cli.mcp_server import _argument_type_errors

	reference = "a" * 32
	assert _build_args("boss_clean", {"pending_claims": reference}) == ["clean", "--pending-claims", reference]
	assert _argument_type_errors("boss_clean", {"pending_claims": True})
	assert _argument_type_errors("boss_clean", {"pending_claims": ""})


def test_legacy_claim_migration_preserves_pending_and_sent(tmp_path: Path) -> None:
	db = tmp_path / "cache" / "boss_agent.db"
	db.parent.mkdir()
	with sqlite3.connect(db) as conn:
		conn.execute("CREATE TABLE candidate_action_claims (account TEXT, job_id TEXT, action TEXT, security_id TEXT, status TEXT, updated_at REAL, PRIMARY KEY(account, job_id, action))")
		conn.executemany("INSERT INTO candidate_action_claims VALUES ('', ?, 'greet', 'S', ?, 1)", [("old-pending", "pending"), ("old-sent", "sent")])
	with CacheStore(db) as cache:
		assert cache.claim_candidate_action("S", "old-pending", "greet") == "pending"
		assert cache.claim_candidate_action("S", "old-sent", "greet") == "sent"
		assert cache.candidate_action_release_reference("old-pending", "greet") is None
		assert cache.claim_candidate_action("S", "new", "greet") is None
		assert len(cache.candidate_action_release_reference("new", "greet") or "") == 32


@pytest.mark.parametrize("action", ["greet", "apply"])
def test_cli_recovery_reference_clears_only_failed_claim_then_retry_succeeds(tmp_path: Path, action: str) -> None:
	platform = _cli_platform()
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.claim_candidate_action("other-sec", "other-job", action)
	with (
		patch(f"boss_agent_cli.commands.{action}.AuthManager"),
		patch(f"boss_agent_cli.commands.{action}.get_platform_instance", return_value=platform),
	):
		with patch.object(CacheStore, "release_candidate_action", side_effect=sqlite3.OperationalError("locked")):
			failed = _invoke(tmp_path, action, "sec-1", "job-1")
		assert failed.exit_code == 1, failed.output
		error = json.loads(failed.stdout)["error"]
		reference = error["details"]["claim_reference"]
		assert reference in error["recovery_action"]
		cleaned = _invoke(tmp_path, "clean", "--pending-claims", reference)
		assert cleaned.exit_code == 0, cleaned.output
		getattr(platform, action).side_effect = None
		getattr(platform, action).return_value = {"code": 0, "zpData": {}}
		platform.is_success.return_value = True
		retried = _invoke(tmp_path, action, "sec-1", "job-1")
	assert retried.exit_code == 0, retried.output
	assert _claim_state(tmp_path, "job-1", action) == "sent"
	assert _claim_state(tmp_path, "other-job", action) == "pending"
