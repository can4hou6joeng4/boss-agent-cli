"""自动化队列派发字段与 JSONL 写入完整性（Issue #2071–#2073）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.config import AutomationConfig
from boss_agent_cli.automation.models import ActionResult, ConversationRef, PlatformAction
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli

_MISSING = object()


class _Adapter:
	name = "zhipin"

	def __init__(self) -> None:
		self.calls: list[tuple[PlatformAction, str, str]] = []

	def ensure_session(self) -> None:
		pass

	def detect_safety_warning(self) -> None:
		return None

	def scan_conversations(self, tabs: list[str], max_per_tab: int) -> list[ConversationRef]:
		return []

	def execute_action(self, action: PlatformAction, message: str, ref: ConversationRef) -> ActionResult:
		self.calls.append((action, message, ref.id))
		return ActionResult(status="executed")


def _row(row_id: str = "pending-1") -> dict:
	return {
		"id": row_id,
		"ts": "2026-11-19T00:00:00+00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": PlatformAction.SEND_QUESTIONNAIRE.value,
		"status": "pending",
		"confidence": 0.99,
		"reason": "已通过复核",
		"message": "请确认是否方便沟通岗位。",
	}


def _seed(store: AutomationStore, row: dict, *, newline: bool = True) -> bytes:
	payload = (json.dumps(row, ensure_ascii=True) + ("\n" if newline else "")).encode("utf-8")
	store.pending_path.write_bytes(payload)
	return payload


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("action", [PlatformAction.SEND_QUESTIONNAIRE, PlatformAction.SEND_FOLLOW_UP])
@pytest.mark.parametrize(
	("field", "value"),
	[
		pytest.param("candidate_key", _MISSING, id="missing-candidate"),
		pytest.param("candidate_key", "", id="empty-candidate"),
		pytest.param("candidate_key", " \t", id="blank-candidate"),
		pytest.param("message", _MISSING, id="missing-message"),
		pytest.param("message", "", id="empty-message"),
		pytest.param("message", " \t", id="blank-message"),
	],
)
def test_invalid_dispatch_fields_preserve_pending(tmp_path: Path, dry_run: bool, action: PlatformAction, field: str, value) -> None:
	store = AutomationStore(tmp_path)
	row = _row()
	row["action"] = action.value
	if value is _MISSING:
		row.pop(field)
	else:
		row[field] = value
	original = _seed(store, row)
	adapter = _Adapter()

	# 历史记录仍可查询，执行和预览都必须在写入 executing 之前拒绝。
	assert len(store.read_pending()) == 1
	with pytest.raises(AutomationStateError) as exc_info:
		run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=dry_run)

	assert exc_info.value.path == store.pending_path
	assert field in exc_info.value.detail
	assert adapter.calls == []
	assert store.pending_path.read_bytes() == original
	assert store.read_pending()[0].status == "pending"


def test_contact_exchange_does_not_require_message(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	row = _row()
	row["action"] = PlatformAction.EXCHANGE_CONTACT.value
	row.pop("message")
	_seed(store, row)
	adapter = _Adapter()

	run_automation_cycle(adapter, store, AutomationConfig(), platform="zhipin", dry_run=False)

	assert adapter.calls == [(PlatformAction.EXCHANGE_CONTACT, "", "candidate-1")]
	assert store.read_pending()[0].status == "executed"


@pytest.mark.parametrize("other_platform", [False, True])
def test_blocked_or_other_platform_rows_keep_historical_empty_fields(tmp_path: Path, other_platform: bool) -> None:
	store = AutomationStore(tmp_path)
	row = _row()
	row.pop("candidate_key")
	row.pop("message")
	if other_platform:
		row["platform"] = "other-platform"
	_seed(store, row)
	adapter = _Adapter()
	config = AutomationConfig() if other_platform else AutomationConfig(allowed_actions=())

	run_automation_cycle(adapter, store, config, platform="zhipin", dry_run=False)

	assert adapter.calls == []
	assert store.read_pending()[0].status == "pending"


@pytest.mark.parametrize("name", ["pending-actions.jsonl", "human-review-queue.jsonl", "action-log.jsonl"])
@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_append_preserves_unterminated_jsonl_record(tmp_path: Path, name: str, ending: bytes) -> None:
	store = AutomationStore(tmp_path)
	first = _row()
	second = _row("pending-2")
	path = store.root / name
	original = json.dumps(first, ensure_ascii=True).encode("utf-8") + ending
	path.write_bytes(original)

	store._append_jsonl(name, second)

	assert store.read_jsonl(name) == [first, second]
	assert path.read_bytes().startswith(original)
	assert path.read_bytes().endswith(b"\n")


def test_append_to_empty_file_keeps_single_jsonl_record(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.pending_path.write_bytes(b"")
	row = _row()

	store._append_jsonl(store.pending_path.name, row)

	assert store.read_jsonl(store.pending_path.name) == [row]
	assert len(store.pending_path.read_bytes().splitlines()) == 1


def test_rewrite_surrogate_preserves_original_and_cleans_temporary(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	row = _row()
	row["reason"] = "\ud800"
	original = _seed(store, row)
	items = store.read_pending()

	with pytest.raises(AutomationStateError) as exc_info:
		store.write_pending(items)

	assert exc_info.value.path == store.pending_path
	assert isinstance(exc_info.value.__cause__, UnicodeEncodeError)
	assert store.pending_path.read_bytes() == original
	assert list(store.root.glob(f".{store.pending_path.name}.*")) == []


@pytest.mark.parametrize("bad_field", ["candidate_key", "message", "surrogate"])
def test_agent_run_reports_queue_path_without_dispatch(tmp_path: Path, monkeypatch, bad_field: str) -> None:
	store = AutomationStore(tmp_path)
	row = _row()
	if bad_field == "surrogate":
		row["reason"] = "\ud800"
	else:
		row.pop(bad_field)
	original = _seed(store, row)
	adapter = _Adapter()
	monkeypatch.setattr("boss_agent_cli.commands.agent.build_automation_adapter", lambda *args, **kwargs: adapter)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "--role", "recruiter", "agent", "run"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert str(store.pending_path) in payload["error"]["message"]
	assert str(store.pending_path) in payload["error"]["recovery_action"]
	assert adapter.calls == []
	assert store.pending_path.read_bytes() == original
	assert store.read_pending()[0].status == "pending"
