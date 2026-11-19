"""automation 队列回写保留坏行与连续错误计数下界（Issue #2172 / #2173）。

`read_jsonl` 对单行损坏是容错的（stats / pending 不整体失败），但整文件回写
原先只写内存里过滤后的列表：一行截断的「已批准动作」会在下一次 live 派发时
被静默删除。读取路径的 `consecutive_errors` 只校验整数类型，负值让连续失败
熔断需要更多次失败才打开。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.models import PlatformAction, ReviewItem
from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli


def _row(row_id: str) -> dict:
	return {
		"id": row_id,
		"ts": "2026-11-20T00:00:00+00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": PlatformAction.SEND_QUESTIONNAIRE.value,
		"status": "pending",
		"confidence": 0.9,
		"reason": "已通过复核",
		"message": "请确认是否方便沟通岗位。",
	}


# ── Issue #2172：整文件回写保留无法解析的行 ────────────────────────


def test_write_pending_preserves_unparseable_lines(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	torn = b'{"id": "torn", "status": "pend'
	non_object = b"[1, 2, 3]"
	undecodable = b'\xff\xfe{"id": "gbk"}'
	store.pending_path.write_bytes(
		(json.dumps(_row("keep"), ensure_ascii=True) + "\n").encode("utf-8")
		+ torn
		+ b"\n"
		+ non_object
		+ b"\n"
		+ undecodable
		+ b"\n"
	)

	store.write_pending([])

	data = store.pending_path.read_bytes()
	assert torn in data
	assert non_object in data
	assert undecodable in data
	# 合法旧行仍以内存列表为准（这里传入空列表），只有坏行被保留。
	assert b'"keep"' not in data
	assert store.read_jsonl("pending-actions.jsonl") == []


def test_write_jsonl_keeps_order_of_valid_rows_before_preserved_lines(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	torn = b'{"id": "torn"'
	store.pending_path.write_bytes(torn + b"\n")

	store.write_jsonl("pending-actions.jsonl", [_row("new-1"), _row("new-2")])

	lines = store.pending_path.read_bytes().splitlines()
	assert [json.loads(line)["id"] for line in lines[:2]] == ["new-1", "new-2"]
	assert lines[2] == torn


def test_approve_review_preserves_unparseable_review_lines(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	item = ReviewItem(
		id="r1",
		ts="2026-11-20T00:00:00+00:00",
		platform="zhipin",
		candidate_key="candidate-1",
		action=PlatformAction.SEND_QUESTIONNAIRE.value,
		status="review",
		confidence=0.9,
		reason="待复核",
		message="请确认是否方便沟通岗位。",
	)
	store.append_review(item)
	torn = b'{"id": "r2", "status": "rev'
	with store.reviews_path.open("ab") as handle:
		handle.write(torn + b"\n")

	pending = store.approve_review("r1", "2026-11-20T01:00:00+00:00")

	assert pending is not None
	assert torn in store.reviews_path.read_bytes()
	rows = store.read_jsonl("human-review-queue.jsonl")
	assert [row["id"] for row in rows] == ["r1"]
	assert rows[0]["status"] == "approved"


def test_write_pending_fails_closed_when_queue_unreadable(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""读不出旧文件时不能继续回写：那等于把不可见的内容全部覆盖掉。"""
	store = AutomationStore(tmp_path)
	store.pending_path.write_text("{}\n", encoding="utf-8")
	original = Path.read_bytes

	def fake(self: Path, *args, **kwargs):
		if self == store.pending_path:
			raise PermissionError(13, "Permission denied", str(self))
		return original(self, *args, **kwargs)

	monkeypatch.setattr(Path, "read_bytes", fake)

	with pytest.raises(AutomationStateError) as excinfo:
		store.write_pending([])

	assert "回写会丢失无法解析的行" in str(excinfo.value)


# ── Issue #2173：consecutive_errors 必须是非负整数 ─────────────────


@pytest.mark.parametrize("value", [-1, -100])
def test_negative_consecutive_errors_is_state_invalid(tmp_path: Path, value: int) -> None:
	store = AutomationStore(tmp_path)
	store.state_path.write_text(
		json.dumps({"safety": {"consecutive_errors": value}}), encoding="utf-8"
	)

	with pytest.raises(AutomationStateError) as excinfo:
		store.read_state()

	assert "非负整数" in str(excinfo.value)


@pytest.mark.parametrize("value", [0, 3, 10000])
def test_non_negative_consecutive_errors_is_accepted(tmp_path: Path, value: int) -> None:
	store = AutomationStore(tmp_path)
	store.state_path.write_text(
		json.dumps({"safety": {"consecutive_errors": value}}), encoding="utf-8"
	)

	assert store.read_state()["safety"]["consecutive_errors"] == value


def test_agent_stats_reports_negative_counter_as_state_invalid(tmp_path: Path) -> None:
	state_dir = tmp_path / "automation"
	state_dir.mkdir()
	(state_dir / "state.json").write_text(
		json.dumps({"safety": {"consecutive_errors": -2}}), encoding="utf-8"
	)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "stats"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "非负整数" in payload["error"]["message"]
