"""求职者 greet / apply 的跨进程原子预约（Issue #1508）。

覆盖：
- CacheStore 预约语义（only-one-claims / settle / release / 旧记录兼容）；
- CLI ``greet`` / ``apply`` / ``batch-greet`` 在 pending、成功、预算停止、
  结果未知时的信封与本地状态；
- wizard 的 candidate_greet / candidate_apply 共用同一收口。

全部离线：平台与登录态都是 mock，缓存使用真实 SQLite。
"""

import json
import threading
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, resolve_policy
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.candidate_actions import CandidateActionBlocked, run_candidate_action
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError


def _db(tmp_path) -> Any:
	return tmp_path / "cache" / "boss_agent.db"


def _preclaim(tmp_path, job_id: str, action: str, *, status: str = "pending", security_id: str = "sec-1") -> None:
	"""模拟「另一个进程已经发送中（pending）」或「已成功（sent）」。"""
	with CacheStore(_db(tmp_path)) as cache:
		if status == "pending":
			assert cache.claim_candidate_action(security_id, job_id, action) is None
		else:
			cache.mark_candidate_action_sent(security_id, job_id, action)


def _platform() -> MagicMock:
	platform = MagicMock()
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *args: None
	platform.is_success.return_value = True
	platform.greet.return_value = {"code": 0, "zpData": {}}
	platform.apply.return_value = {"code": 0, "zpData": {}}
	return platform


@contextmanager
def _patched(module: str, platform: MagicMock):
	with (
		patch(f"boss_agent_cli.commands.{module}.AuthManager"),
		patch(f"boss_agent_cli.commands.{module}.get_platform_instance") as factory,
	):
		factory.return_value.__enter__ = lambda self: platform
		factory.return_value.__exit__ = lambda self, *args: None
		yield


def _invoke(module: str, platform: MagicMock, tmp_path, args: list[str]):
	with _patched(module, platform):
		return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


# ── CacheStore 预约语义 ─────────────────────────────────────────────


def test_claim_is_atomic_across_connections(tmp_path):
	db = _db(tmp_path)
	CacheStore(db).close()
	barrier = threading.Barrier(2)
	results: list[str | None] = []
	errors: list[BaseException] = []

	def worker() -> None:
		store = CacheStore(db)
		try:
			barrier.wait(timeout=10)
			results.append(store.claim_candidate_action("sec", "job", "greet"))
		except BaseException as exc:
			errors.append(exc)
		finally:
			store.close()

	threads = [threading.Thread(target=worker) for _ in range(2)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join(timeout=10)

	assert errors == []
	assert sorted(results, key=lambda value: value or "") == [None, "pending"]


def test_settle_and_release_round_trip(tmp_path):
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.claim_candidate_action("sec", "job", "greet") is None
		assert cache.claim_candidate_action("sec", "job", "greet") == "pending"
		cache.record_greet("sec", "job")
		cache.mark_candidate_action_sent("sec", "job", "greet")
		assert cache.claim_candidate_action("sec", "job", "greet") == "sent"

		assert cache.claim_candidate_action("sec", "job-2", "apply") is None
		assert cache.release_candidate_action("job-2", "apply") is True
		# sent 记录不会被 release 撤销
		assert cache.release_candidate_action("job", "greet") is False
		assert cache.claim_candidate_action("sec", "job-2", "apply") is None


def test_legacy_records_still_block_new_claims(tmp_path):
	"""旧 greet/apply 记录表没有新预约行时，历史去重仍然生效。"""
	with CacheStore(_db(tmp_path)) as cache:
		cache.record_greet("legacy-sec", "job-greet")
		cache.record_apply("legacy-sec", "job-apply")
		assert cache.claim_candidate_action("legacy-sec", "job-greet", "greet") == "sent"
		assert cache.claim_candidate_action("legacy-sec", "job-apply", "apply") == "sent"
		# 不同账号（指纹不同）不误拦
		assert cache.claim_candidate_action("legacy-sec", "job-greet", "greet", account="acct-b") is None


def test_concurrent_run_candidate_action_sends_once(tmp_path):
	db = _db(tmp_path)
	CacheStore(db).close()
	barrier = threading.Barrier(2)
	sends: list[str] = []
	blocked: list[str] = []
	errors: list[BaseException] = []

	def worker() -> None:
		store = CacheStore(db)
		try:
			barrier.wait(timeout=10)
			run_candidate_action(
				store,
				security_id="sec",
				job_id="job",
				action="greet",
				send=lambda: (time.sleep(0.05), sends.append("sent"))[1],
			)
		except CandidateActionBlocked as exc:
			blocked.append(exc.code)
		except BaseException as exc:  # pragma: no cover
			errors.append(exc)
		finally:
			store.close()

	threads = [threading.Thread(target=worker) for _ in range(2)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join(timeout=10)

	assert errors == []
	assert sends == ["sent"]
	assert blocked == ["ACTION_UNCONFIRMED"]


# ── CLI greet ──────────────────────────────────────────────────────


def test_cli_greet_pending_claim_returns_action_unconfirmed(tmp_path):
	_preclaim(tmp_path, "job-1", "greet")
	platform = _platform()

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["replay_safe"] is False
	platform.greet.assert_not_called()


def test_cli_greet_success_settles_claim_and_legacy_record(tmp_path):
	platform = _platform()

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 0, result.output
	platform.greet.assert_called_once_with("sec-1", "job-1", "")
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.is_greeted("sec-1", job_id="job-1") is True
		assert cache.claim_candidate_action("sec-2", "job-1", "greet") == "sent"


def test_cli_greet_releases_claim_when_budget_stopped_before_send(tmp_path):
	platform = _platform()
	platform.greet.side_effect = RequestBudgetExceeded("fixture budget stop", code="REQUEST_BUDGET_EXHAUSTED")

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert body["error"]["details"]["request_sent"] is False
	with CacheStore(_db(tmp_path)) as cache:
		# 证明未发出：预约被撤销，修正预算后可以重新发送
		assert cache.claim_candidate_action("sec-1", "job-1", "greet") is None


def test_cli_greet_keeps_pending_on_unknown_transport_error(tmp_path):
	platform = _platform()
	platform.greet.side_effect = TimeoutError("response timed out")

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	with CacheStore(_db(tmp_path)) as cache:
		# 超时可能已经产生写入：保留 pending，重跑会被 ACTION_UNCONFIRMED 拦下
		assert cache.claim_candidate_action("sec-1", "job-1", "greet") == "pending"


def test_cli_greet_legacy_record_blocks_before_platform_call(tmp_path):
	with CacheStore(_db(tmp_path)) as cache:
		cache.record_greet("sec-1", "job-1")
	platform = _platform()

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "ALREADY_GREETED"
	platform.greet.assert_not_called()


def test_cli_greet_already_sent_claim_blocks_repeat(tmp_path):
	_preclaim(tmp_path, "job-1", "greet", status="sent")
	platform = _platform()

	result = _invoke("greet", platform, tmp_path, ["greet", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "ALREADY_GREETED"
	platform.greet.assert_not_called()


# ── CLI apply ──────────────────────────────────────────────────────


def test_cli_apply_pending_claim_returns_action_unconfirmed(tmp_path):
	_preclaim(tmp_path, "job-1", "apply")
	platform = _platform()

	result = _invoke("apply", platform, tmp_path, ["apply", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["details"]["replay_safe"] is False
	platform.apply.assert_not_called()


def test_cli_apply_success_settles_claim_and_legacy_record(tmp_path):
	platform = _platform()

	result = _invoke("apply", platform, tmp_path, ["apply", "sec-1", "job-1"])

	assert result.exit_code == 0, result.output
	platform.apply.assert_called_once_with("sec-1", "job-1", lid="")
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.is_applied("sec-1", "job-1") is True
		assert cache.claim_candidate_action("sec-2", "job-1", "apply") == "sent"


def test_cli_apply_releases_claim_on_provably_unsent_channel_error(tmp_path):
	platform = _platform()
	platform.apply.side_effect = BrowserSourceUnavailable(resolve_policy("stored-cookie"), attempted=("cdp",))

	result = _invoke("apply", platform, tmp_path, ["apply", "sec-1", "job-1"])

	assert result.exit_code == 1, result.output
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.claim_candidate_action("sec-1", "job-1", "apply") is None


# ── CLI batch-greet ────────────────────────────────────────────────

_RAW_JOB = {
	"encryptJobId": "job-1",
	"securityId": "sec-1",
	"jobName": "后端开发",
	"brandName": "TestCo",
	"salaryDesc": "20K",
	"cityName": "北京",
	"jobExperience": "3-5年",
	"jobDegree": "本科",
	"bossName": "张",
	"bossTitle": "HR",
}


def _batch_platform() -> MagicMock:
	platform = _platform()
	platform.name = "zhipin"
	platform.search_jobs.return_value = {"code": 0, "zpData": {"jobList": [dict(_RAW_JOB)]}}
	platform.unwrap_data.side_effect = lambda payload: payload.get("zpData", payload)
	return platform


def test_batch_greet_pending_claim_stops_without_resend(tmp_path):
	_preclaim(tmp_path, "job-1", "greet")
	platform = _batch_platform()

	result = _invoke("greet", platform, tmp_path, ["batch-greet", "golang", "--count", "1"])

	assert result.exit_code == 1, result.output
	body = json.loads(result.stdout)
	assert body["error"]["code"] == "ACTION_UNCONFIRMED"
	assert body["error"]["details"]["replay_safe"] is False
	platform.greet.assert_not_called()


def test_batch_greet_sent_claim_is_skipped(tmp_path):
	_preclaim(tmp_path, "job-1", "greet", status="sent")
	platform = _batch_platform()

	result = _invoke("greet", platform, tmp_path, ["batch-greet", "golang", "--count", "1"])

	assert result.exit_code == 0, result.output
	body = json.loads(result.stdout)
	assert body["data"]["total_greeted"] == 0
	platform.greet.assert_not_called()


def test_batch_greet_success_settles_claim(tmp_path):
	platform = _batch_platform()

	result = _invoke("greet", platform, tmp_path, ["batch-greet", "golang", "--count", "1"])

	assert result.exit_code == 0, result.output
	platform.greet.assert_called_once()
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.claim_candidate_action("sec-2", "job-1", "greet") == "sent"


# ── wizard ─────────────────────────────────────────────────────────


class _FakeCandidatePlatform:
	def __init__(self) -> None:
		self.calls: list[tuple] = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def greet(self, security_id, job_id, message=""):
		self.calls.append(("greet", security_id, job_id, message))
		return {"code": 0, "data": {"greeted": True}}

	def apply(self, security_id, job_id, lid=""):
		self.calls.append(("apply", security_id, job_id, lid))
		return {"code": 0, "data": {"applied": True}}

	def is_success(self, response) -> bool:
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "UNKNOWN", "boom"


def _action_context(tmp_path, platform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args: platform,
	)


def test_wizard_candidate_greet_pending_claim_is_unconfirmed(tmp_path):
	_preclaim(tmp_path, "job-1", "greet")
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})

	assert excinfo.value.code == "ACTION_UNCONFIRMED"
	assert excinfo.value.replay_safe is False
	assert excinfo.value.recoverable is False
	assert platform.calls == []


def test_wizard_candidate_greet_success_settles_and_blocks_repeat(tmp_path):
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-1", "job_id": "job-1", "message": "你好"}, {})
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_greet"](context, {"security_id": "sec-2", "job_id": "job-1"}, {})

	assert excinfo.value.code == "ALREADY_GREETED"
	assert platform.calls == [("greet", "sec-1", "job-1", "你好")]
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.claim_candidate_action("sec-2", "job-1", "greet") == "sent"


def test_wizard_candidate_apply_pending_claim_is_unconfirmed(tmp_path):
	_preclaim(tmp_path, "job-1", "apply")
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})

	assert excinfo.value.code == "ACTION_UNCONFIRMED"
	assert excinfo.value.replay_safe is False
	assert platform.calls == []


def test_wizard_candidate_apply_success_settles_and_blocks_repeat(tmp_path):
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-1", "job_id": "job-1", "lid": "l1"}, {})
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-2", "job_id": "job-1"}, {})

	assert excinfo.value.code == "ALREADY_APPLIED"
	assert platform.calls == [("apply", "sec-1", "job-1", "l1")]


@pytest.mark.parametrize("lid", [123, True, ["l1"], {"a": 1}])
def test_wizard_candidate_apply_rejects_non_string_lid(tmp_path, lid):
	"""非字符串 lid 不得转成 repr 后进入投递写请求，也不得留下本地预约。"""
	from boss_agent_cli.wizard.models import WorkflowInputError

	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	with pytest.raises(WorkflowInputError, match="lid 必须是字符串"):
		DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-1", "job_id": "job-1", "lid": lid}, {})

	assert platform.calls == []
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.is_applied("sec-1", "job-1") is False


@pytest.mark.parametrize("inputs", [{}, {"lid": None}, {"lid": ""}])
def test_wizard_candidate_apply_treats_absent_lid_as_empty(tmp_path, inputs):
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, platform)

	DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-1", "job_id": "job-1", **inputs}, {})

	assert len(platform.calls) == 1
