"""审计批次六十一：面试线索 CSV 的崩溃窗口与 automation 序列化错误分类（Issue #2331、#2332）。

- #2331：表头原子写入；只有首行确为表头才跳过判重；0 字节文件补写表头；
  末缺换行先补 ``\\n``，残表头 / 无换行末行不再与新行粘连；
- #2332：JSON / JSONL 写入器把 ``TypeError`` 与 ``ValueError`` 同口径归为
  ``AutomationStateError``，不裸穿成 ``INTERNAL_ERROR``。

全部离线：只操作 tmp_path 下的本地文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore

_HEADER = "candidate_key,interview_time,reason\n"


def _store(tmp_path: Path) -> AutomationStore:
	return AutomationStore(tmp_path)


def _leads_path(store: AutomationStore) -> Path:
	return store.root / "interview-leads.csv"


# ── #2331：面试线索 CSV 的表头与去重 ─────────────────────────────


def test_empty_lead_file_gets_header_and_dedup_works(tmp_path: Path) -> None:
	"""0 字节文件（表头写入被中断）不能把第一条数据行当表头。"""
	store = _store(tmp_path)
	path = _leads_path(store)
	path.write_bytes(b"")

	assert store.append_interview_lead("张三", "2026-01-01", "r") is True
	assert store.append_interview_lead("张三", "2026-01-01", "r") is False

	content = path.read_text(encoding="utf-8")
	assert content.startswith(_HEADER)
	data_lines = [line for line in content.splitlines() if line and not line.startswith("candidate_key")]
	assert len(data_lines) == 1, content


def test_truncated_header_does_not_glue_data_row(tmp_path: Path) -> None:
	"""半截表头（无换行）与新行之间必须补换行。"""
	store = _store(tmp_path)
	path = _leads_path(store)
	path.write_bytes(b"candidate_key,interview_time,reason")

	assert store.append_interview_lead("张三", "2026-01-01", "r") is True

	content = path.read_text(encoding="utf-8")
	assert "reason张三" not in content
	assert content.endswith("张三,2026-01-01,r\r\n") or content.endswith("张三,2026-01-01,r\n")


def test_missing_trailing_newline_is_repaired(tmp_path: Path) -> None:
	store = _store(tmp_path)
	path = _leads_path(store)
	path.write_text(_HEADER + "李四,2026-01-01,old", encoding="utf-8")

	assert store.append_interview_lead("张三", "2026-01-02", "r") is True

	lines = path.read_text(encoding="utf-8").splitlines()
	assert "李四,2026-01-01,old" in lines
	assert "张三,2026-01-02,r" in lines


def test_headerless_file_dedup_includes_first_data_row(tmp_path: Path) -> None:
	"""无表头文件的第一行是数据，必须参与判重。"""
	store = _store(tmp_path)
	path = _leads_path(store)
	path.write_text("张三,2026-01-01,r\n", encoding="utf-8")

	assert store.append_interview_lead("张三", "2026-01-01", "r") is False


def test_normal_append_and_dedup_still_work(tmp_path: Path) -> None:
	store = _store(tmp_path)
	path = _leads_path(store)

	assert store.append_interview_lead("张三", "2026-01-01", "r") is True
	assert store.append_interview_lead("张三", "2026-01-01", "r") is False
	assert store.append_interview_lead("张三", "2026-02-01", "r") is True

	lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
	assert lines[0] == "candidate_key,interview_time,reason"
	assert len(lines) == 3


# ── #2332：序列化 TypeError 归为 AutomationStateError ────────────


def test_write_jsonl_wraps_unserializable_value(tmp_path: Path) -> None:
	store = _store(tmp_path)

	with pytest.raises(AutomationStateError) as caught:
		store.write_jsonl("x.jsonl", [{"x": b"y"}])

	assert "无法序列化" in caught.value.detail
	assert "TypeError" in caught.value.detail


def test_write_json_atomic_wraps_mixed_key_types(tmp_path: Path) -> None:
	store = _store(tmp_path)

	with pytest.raises(AutomationStateError) as caught:
		store._write_json_atomic(store.root / "s.json", {1: "a", "b": 2})

	assert "无法序列化" in caught.value.detail
	assert "TypeError" in caught.value.detail


def test_append_jsonl_wraps_unserializable_value(tmp_path: Path) -> None:
	store = _store(tmp_path)

	with pytest.raises(AutomationStateError) as caught:
		store._append_jsonl("x.jsonl", {"x": b"y"})

	assert "not JSON serializable" in caught.value.detail
