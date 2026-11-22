"""审计批次 123：不可编码的内部 run_id 与沟通导出文本（Issue #2495 / #2496）。

- #2495：断点 JSON 列里的孤立代理字符能通过读取校验，再绑定给 sqlite 时抛
  ``UnicodeEncodeError``，拖垮向导状态 / 列表 / 停止控制路径；
- #2496：沟通导出只有 JSON 分支归一代理字符，md / html 返回值不可 UTF-8 编码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.live_crawl import bindable_run_id
from boss_agent_cli.wizard.models import WizardInput
from boss_agent_cli.wizard.store import WorkflowStore

_MAPPING = {
	"role": "candidate",
	"platform": "zhipin",
	"goal": "crawl_start",
	"inputs": {"query": "Python", "city": "北京"},
}


def _plan():
	return build_plan(WizardInput.from_mapping(_MAPPING))


def test_bindable_run_id_rejects_unencodable_values() -> None:
	assert bindable_run_id("run-1") == "run-1"
	assert bindable_run_id(123) == "123"
	assert bindable_run_id("\ud800") is None
	assert bindable_run_id(10**4400) is None
	assert bindable_run_id(None) is None


def _seed_last_result(store: WorkflowStore, run_id: str, payload: dict) -> None:
	store.cache._conn.execute(
		"UPDATE workflow_runs SET last_result = ? WHERE run_id = ?",
		(json.dumps(payload), run_id),
	)
	store.cache._conn.commit()


def _seed_step(store: WorkflowStore, run_id: str, step_name: str, result: dict) -> None:
	store.cache._conn.execute(
		"INSERT INTO workflow_steps (run_id, step_name, ordinal, status, result, error) VALUES (?, ?, ?, ?, ?, ?)",
		(run_id, step_name, 0, "completed", json.dumps(result), None),
	)
	store.cache._conn.commit()


def test_status_and_list_survive_unencodable_inner_run_id(tmp_path: Path) -> None:
	"""一条 run 的内部 id 编不了码，不能把整份状态 / 列表拖垮（#2495）。"""
	with WorkflowStore(tmp_path) as store:
		store.create("runX", _plan())
		_seed_last_result(store, "runX", {"data": {"run_id": "\ud800"}})

		checkpoint = store.get_checkpoint("runX")
		assert checkpoint is not None
		assert checkpoint["run_id"] == "runX"

		for run in (store.get("runX"), store.get_for_result("runX")):
			assert run is not None
			assert run["run_id"] == "runX"
			# 不可寻址的内部任务按「没有绑定」处理：不叠加实时采集字段。
			assert "live_crawl_status" not in run

		recent = store.list_recent()
		assert [run["run_id"] for run in recent] == ["runX"]
		assert store.corrupt_run_ids == []


def test_request_stop_skips_unencodable_inner_run_id_and_keeps_stopping(tmp_path: Path) -> None:
	"""停止已落盘，不能因为某个内部 id 编不了码就报失败（#2495）。"""
	with WorkflowStore(tmp_path) as store:
		store.create("runY", _plan())
		store.cache.create_crawl_run("crawl-ok", {"query": "Python"}, str(tmp_path))
		_seed_step(store, "runY", "crawl", {"inner_run_id": "\ud800"})
		store.cache._conn.execute(
			"INSERT INTO workflow_steps (run_id, step_name, ordinal, status, result, error) VALUES (?, ?, ?, ?, ?, ?)",
			("runY", "crawl-2", 1, "completed", json.dumps({"inner_run_id": "crawl-ok"}), None),
		)
		store.cache._conn.commit()

		assert store.request_stop("runY") is True

		assert store.get_run_status("runY") == "stopped"
		crawl = store.cache.get_crawl_run("crawl-ok")
		assert crawl is not None
		# 跳过不可编码的步骤后，其余绑定仍要收到停止请求。
		assert crawl["stop_requested"] is True


def _friends() -> list[dict[str, object]]:
	return [
		{
			"uid": "\ud800",
			"name": "示例联系人",
			"brand_name": "示例公司",
			"initiated_by": "对方主动",
			"last_msg": "你好",
			"unread": 1,
		}
	]


@pytest.mark.parametrize("fmt", ["md", "html", "csv", "json"])
def test_chat_export_returns_utf8_encodable_text(fmt: str) -> None:
	"""所有格式的返回值都必须能按严格 UTF-8 编码（#2496）。"""
	text = render_export(_friends(), fmt, None, None, {})
	text.encode("utf-8")


@pytest.mark.parametrize("fmt", ["md", "html"])
def test_chat_export_sanitizes_surrogate_prev_date(fmt: str) -> None:
	text = render_export(
		[{"uid": 1, "name": "示例联系人"}],
		fmt,
		None,
		None,
		{"is_first": False, "prev_date": "\ud800"},
	)
	text.encode("utf-8")
	assert "\\ud800" in text
