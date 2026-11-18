"""批次二十八回归测试：Issue #1667–#1668。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.commands.search import _parse_cached_result
from boss_agent_cli.main import cli


def _pending_row(**overrides):
	row = {
		"id": "p1",
		"ts": "2026-11-18T00:00:00",
		"platform": "zhipin",
		"candidate_key": "candidate-1",
		"action": "skip",
		"status": "pending",
		"confidence": 0.9,
		"reason": "",
		"message": "",
	}
	row.update(overrides)
	return row


def _write_pending(tmp_path: Path, rows) -> AutomationStore:
	store = AutomationStore(tmp_path)
	store.pending_path.write_text(
		"".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
		encoding="utf-8",
	)
	return store


@pytest.mark.parametrize("confidence", ["NaN-ish", True, None, [0.9]])
def test_stats_validates_pending_confidence_like_pending_list(tmp_path: Path, confidence) -> None:
	store = _write_pending(tmp_path, [_pending_row(confidence=confidence)])

	with pytest.raises(ValueError, match="confidence"):
		store.stats()


@pytest.mark.parametrize("status", [None, 1, "Pending", ["pending"]])
def test_stats_validates_pending_status_like_pending_list(tmp_path: Path, status) -> None:
	store = _write_pending(tmp_path, [_pending_row(status=status)])

	with pytest.raises(ValueError, match="status"):
		store.stats()


def test_agent_stats_reports_drifted_pending_queue(tmp_path: Path) -> None:
	_write_pending(tmp_path, [_pending_row(confidence="NaN-ish")])
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "stats"])
	assert result.exit_code == 1
	assert json.loads(result.output)["error"]["code"] == "AUTOMATION_STATE_INVALID"


def test_stats_validates_review_rows(tmp_path: Path) -> None:
	store = AutomationStore(tmp_path)
	store.reviews_path.write_text(
		json.dumps({"id": "r1", "status": "review", "confidence": "NaN-ish"}) + "\n",
		encoding="utf-8",
	)
	with pytest.raises(ValueError, match="confidence"):
		store.stats()


def test_stats_counts_valid_rows_unchanged(tmp_path: Path) -> None:
	store = _write_pending(tmp_path, [_pending_row(), _pending_row(id="p2", status="executed")])
	store.reviews_path.write_text(
		json.dumps({"id": "r1", "status": "review", "confidence": 0.8}) + "\n",
		encoding="utf-8",
	)
	stats = store.stats()
	assert stats["pending_actions"] == 1
	assert stats["human_reviews"] == 1


@pytest.mark.parametrize("raw", [123, 1.5, None, True, [], {}, object()])
def test_parse_cached_result_rejects_non_text_payload(raw) -> None:
	assert _parse_cached_result(raw) is None  # type: ignore[arg-type]


def test_parse_cached_result_accepts_bytes() -> None:
	parsed = _parse_cached_result(b'{"data": [{"title": "ok"}]}')
	assert parsed is not None
	assert parsed["data"] == [{"title": "ok"}]
