"""Issue #3081：向导采集状态面板的本地路径 / 绑定标识按真实类型 fail-closed。

- ``_output_paths`` 只按 ``lstat()`` 存在性判断产物：``jobs.json`` 是目录、
  ``jobs.csv`` 是悬空链接时仍向状态面板报告「产物存在」，展示的是打不开的路径。
- ``bindable_run_id`` 对容器返回 ``str()`` repr（``"{'x': 1}"``），
  ``inner_crawl_run_id`` 立即 return，合法的 ``params.inputs.run_id`` 回退被
  截断，状态面板的 jobs / output_paths 全部消失。
"""

from __future__ import annotations

import os

import pytest

from boss_agent_cli.wizard.live_crawl import _output_paths, bindable_run_id, inner_crawl_run_id


def test_output_paths_skips_directory_and_dangling_link(tmp_path) -> None:
	(tmp_path / "jobs.json").mkdir()
	os.symlink(str(tmp_path / "missing-target.json"), tmp_path / "jobs.csv")
	(tmp_path / "jobs.xlsx").write_text("x")

	assert _output_paths(str(tmp_path)) == {"xlsx": str(tmp_path / "jobs.xlsx")}


def test_output_paths_skips_fifo(tmp_path) -> None:
	if not hasattr(os, "mkfifo"):
		pytest.skip("平台不支持 mkfifo")
	os.mkfifo(tmp_path / "jobs.json")
	(tmp_path / "jobs.csv").write_text("x")

	assert _output_paths(str(tmp_path)) == {"csv": str(tmp_path / "jobs.csv")}


def test_output_paths_accepts_symlink_to_regular_file(tmp_path) -> None:
	target = tmp_path / "real.json"
	target.write_text("{}")
	os.symlink(str(target), tmp_path / "jobs.json")

	assert _output_paths(str(tmp_path)) == {"json": str(tmp_path / "jobs.json")}


def test_inner_run_id_falls_back_when_last_result_is_container() -> None:
	run = {
		"goal": "crawl_start",
		"last_result": {"data": {"run_id": {"x": 1}}},
		"params": {"inputs": {"run_id": "real-crawl"}},
	}

	assert inner_crawl_run_id(run) == "real-crawl"


def test_inner_run_id_none_when_all_candidates_drifted() -> None:
	run = {
		"last_result": {"data": {"run_id": ["x"]}},
		"params": {"inputs": {"run_id": {"y": 1}, "crawl_run_id": None}},
	}

	assert inner_crawl_run_id(run) is None


def test_bindable_run_id_rejects_container_and_bool() -> None:
	assert bindable_run_id({"x": 1}) is None
	assert bindable_run_id(["x"]) is None
	assert bindable_run_id(True) is None
	assert bindable_run_id(False) is None


def test_bindable_run_id_keeps_numeric_scalar_contract() -> None:
	assert bindable_run_id(123) == "123"
	assert bindable_run_id("run-1") == "run-1"
