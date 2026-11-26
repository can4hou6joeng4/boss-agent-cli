"""候选池重复加入时省略来源不得覆盖既有来源（Issue #2960）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "shortlist", *args])


def _data(result) -> dict:
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)["data"]


@pytest.fixture()
def store(tmp_path: Path):
	cache = CacheStore(tmp_path / "cache" / "test.db")
	yield cache
	cache.close()


def test_omitted_source_preserves_existing_source(tmp_path: Path) -> None:
	first = _data(_invoke(tmp_path, "add", "S", "J", "--title", "T", "--source", "favorites"))
	assert first["source"] == "favorites"

	second = _data(_invoke(tmp_path, "add", "S", "J"))
	assert second["source"] == "favorites"
	assert second["title"] == "T"

	listed = _data(_invoke(tmp_path, "list"))
	assert [(item["security_id"], item["job_id"], item["source"]) for item in listed] == [
		("S", "J", "favorites"),
	]


def test_new_record_without_source_is_manual(tmp_path: Path) -> None:
	stored = _data(_invoke(tmp_path, "add", "S", "J"))
	assert stored["source"] == "manual"


def test_explicit_source_still_overwrites(tmp_path: Path) -> None:
	_data(_invoke(tmp_path, "add", "S", "J", "--source", "favorites"))

	stored = _data(_invoke(tmp_path, "add", "S", "J", "--source", "search"))

	assert stored["source"] == "search"


def test_store_treats_missing_source_as_unspecified(store) -> None:
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "source": "favorites"})
	store.add_shortlist({"security_id": "s1", "job_id": "j1"})
	assert store.get_shortlist_item("s1", "j1")["source"] == "favorites"

	# 显式空串仍是“调用方给出的值”：冲突时保留原值，新记录保持空串。
	store.add_shortlist({"security_id": "s1", "job_id": "j1", "source": ""})
	assert store.get_shortlist_item("s1", "j1")["source"] == "favorites"
	store.add_shortlist({"security_id": "s2", "job_id": "j2", "source": ""})
	assert store.get_shortlist_item("s2", "j2")["source"] == ""


def test_batch_refresh_still_applies_after_manual_readd(store) -> None:
	"""手工重加不再洗掉 favorites 来源，后续收藏导入仍能刷新轮换的标识。"""
	store.add_shortlist({"security_id": "sid-old", "job_id": "j1", "source": "favorites"})
	store.add_shortlist({"security_id": "sid-old", "job_id": "j1"})

	result = store.add_shortlist_batch([{"security_id": "sid-new", "job_id": "j1"}])

	assert result == {"imported_count": 0, "existing_count": 1, "skipped_count": 0}
	assert store.get_shortlist_item("sid-new", "j1")["source"] == "favorites"


def test_schema_does_not_declare_manual_as_implicit_default() -> None:
	"""工具目录不能把 manual 声明成默认值：宿主省略参数时不应下发 --source。"""
	from boss_agent_cli.schema.data import SCHEMA_DATA

	source = SCHEMA_DATA["commands"]["shortlist"]["options"]["add"]["--source"]
	assert source["default"] is None
