"""审计批次 125：候选池写入的不可编码文本（Issue #2502）。

两处爆点同源：平台 JSON 里的 ``"\\ud800"`` 解码后是孤立代理项，sqlite3 绑定 /
严格 UTF-8 编码会抛 ``UnicodeEncodeError``。argv 入口（``add`` / ``annotate``
/ ``remove``）报 ``INVALID_PARAM``，不再兜成带磁盘指引的 ``LOCAL_IO_ERROR``；
平台数据路径（收藏同步 / crawl 导入）在存储层按字面转义归一，单个字段漂移
不丢整批记录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore, CacheWriteRejected
from boss_agent_cli.crawler.operations import import_crawl_shortlist
from boss_agent_cli.main import cli

_BAD = "\ud800"
_BAD_LITERAL = "\\ud800"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "shortlist", *args])


def _error(result) -> dict:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload["error"]


def _db(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


@pytest.mark.parametrize(
	("label", "args"),
	[
		("security_id", ("add", _BAD, "job-1")),
		("job_id", ("add", "sec-1", _BAD)),
		("--title", ("add", "sec-1", "job-1", "--title", _BAD)),
		("--company", ("add", "sec-1", "job-1", "--company", _BAD)),
		("--city", ("add", "sec-1", "job-1", "--city", _BAD)),
		("--salary", ("add", "sec-1", "job-1", "--salary", _BAD)),
		("--source", ("add", "sec-1", "job-1", "--source", _BAD)),
		("--lid", ("add", "sec-1", "job-1", "--lid", _BAD)),
		("--note", ("add", "sec-1", "job-1", "--note", _BAD)),
	],
	ids=[
		"security_id",
		"job_id",
		"title",
		"company",
		"city",
		"salary",
		"source",
		"lid",
		"note",
	],
)
def test_add_rejects_unencodable_fields(tmp_path: Path, label: str, args: tuple[str, ...]) -> None:
	error = _error(_invoke(tmp_path, *args))

	assert error["code"] == "INVALID_PARAM"
	assert label in error["message"]
	assert "UTF-8" in error["message"]
	assert "磁盘" not in error["recovery_action"]
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.list_shortlist() == []


@pytest.mark.parametrize(
	("label", "args"),
	[
		("security_id", ("annotate", _BAD, "job-1", "--note", "x")),
		("job_id", ("annotate", "sec-1", _BAD, "--note", "x")),
		("--note", ("annotate", "sec-1", "job-1", "--note", _BAD)),
	],
	ids=["security_id", "job_id", "note"],
)
def test_annotate_rejects_unencodable_fields(tmp_path: Path, label: str, args: tuple[str, ...]) -> None:
	error = _error(_invoke(tmp_path, *args))

	assert error["code"] == "INVALID_PARAM"
	assert label in error["message"]
	assert "UTF-8" in error["message"]
	assert "磁盘" not in error["recovery_action"]


@pytest.mark.parametrize(
	"args",
	[("remove", _BAD, "job-1"), ("remove", "sec-1", _BAD)],
	ids=["security_id", "job_id"],
)
def test_remove_rejects_unencodable_fields(tmp_path: Path, args: tuple[str, ...]) -> None:
	error = _error(_invoke(tmp_path, *args))

	assert error["code"] == "INVALID_PARAM"
	assert "UTF-8" in error["message"]
	assert "磁盘" not in error["recovery_action"]


def test_add_shortlist_escapes_unencodable_platform_text(tmp_path: Path) -> None:
	# 收藏同步 / crawl 导入不走 argv 校验，存储层必须自己归一。
	with CacheStore(_db(tmp_path)) as cache:
		stored = cache.add_shortlist(
			{
				"security_id": "sec-1",
				"job_id": "job-1",
				"lid": _BAD,
				"title": f"bad{_BAD}title",
				"company": f"bad{_BAD}company",
				"city": f"bad{_BAD}city",
				"salary": f"bad{_BAD}salary",
				"source": f"bad{_BAD}source",
				"note": f"bad{_BAD}note",
			}
		)
		items = cache.list_shortlist()

	assert stored["title"] == f"bad{_BAD_LITERAL}title"
	assert stored["company"] == f"bad{_BAD_LITERAL}company"
	assert stored["city"] == f"bad{_BAD_LITERAL}city"
	assert stored["salary"] == f"bad{_BAD_LITERAL}salary"
	assert stored["source"] == f"bad{_BAD_LITERAL}source"
	assert stored["note"] == f"bad{_BAD_LITERAL}note"
	assert stored["lid"] == ""
	assert items == [stored]


def test_malformed_single_add_cannot_overwrite_valid_identifier(tmp_path: Path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		original = cache.add_shortlist({"security_id": "sec-1", "job_id": f"job{_BAD_LITERAL}", "title": "原有职位"})
		with pytest.raises(CacheWriteRejected):
			cache.add_shortlist({"security_id": "sec-1", "job_id": f"job{_BAD}", "title": "另一职位"})
		assert cache.list_shortlist() == [original]


def test_batch_skips_malformed_identifier_without_refreshing_valid_row(tmp_path: Path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		original = cache.add_shortlist({"security_id": "sec-1", "job_id": f"job{_BAD_LITERAL}", "source": "favorites"})
		result = cache.add_shortlist_batch([
			{"security_id": "sec-2", "job_id": f"job{_BAD}"},
			{"security_id": "sec-3", "job_id": "job-3"},
		])
		assert cache.get_shortlist_item("sec-1", f"job{_BAD_LITERAL}") == original
	assert result == {"imported_count": 1, "existing_count": 0, "skipped_count": 1}


def test_crawl_skips_malformed_identifier_and_does_not_reuse_malformed_lid(tmp_path: Path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run("run-1", {}, str(tmp_path / "out"))
		cache.put_crawl_job("run-1", "bad", 1, {"job_id": "bad", "security_id": _BAD}, detail_done=False)
		cache.put_crawl_job(
			"run-1", "job-1", 1, {"job_id": "job-1", "security_id": "sec-1", "lid": _BAD}, detail_done=False
		)
		result = import_crawl_shortlist(cache, "run-1", include_all=True)
		items = cache.list_shortlist()

	assert result["imported_count"] == 1
	assert result["skipped_count"] == 1
	assert [item["job_id"] for item in items] == ["job-1"]
	assert items[0]["lid"] == ""


def test_add_shortlist_batch_keeps_other_rows_when_one_field_drifts(tmp_path: Path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		result = cache.add_shortlist_batch(
			[
				{"security_id": "sec-1", "job_id": "job-1", "title": f"bad{_BAD}", "note": f"bad{_BAD}"},
				{"security_id": "sec-2", "job_id": "job-2", "title": "正常职位", "source": "favorites"},
			],
			source="favorites",
		)
		items = {item["job_id"]: item for item in cache.list_shortlist()}

	assert result["imported_count"] == 2
	assert items["job-1"]["title"] == f"bad{_BAD_LITERAL}"
	assert items["job-1"]["note"] == f"bad{_BAD_LITERAL}"
	assert items["job-2"]["title"] == "正常职位"


def test_annotate_storage_normalizes_note_and_tags(tmp_path: Path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache.add_shortlist({"security_id": "sec-1", "job_id": "job-1"})
		assert cache.set_shortlist_note("sec-1", "job-1", f"note{_BAD}") is True
		assert cache.set_shortlist_tags("sec-1", "job-1", [f"tag{_BAD}", "正常标签"]) is True
		item = cache.get_shortlist_item("sec-1", "job-1")

	assert item is not None
	assert item["note"] == f"note{_BAD_LITERAL}"
	assert item["tags"] == [f"tag{_BAD_LITERAL}", "正常标签"]


def test_normal_shortlist_flow_is_unchanged(tmp_path: Path) -> None:
	result = _invoke(tmp_path, "add", "sec-1", "job-1", "--title", "示例岗位", "--note", "关注")
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["data"]["title"] == "示例岗位"

	result = _invoke(tmp_path, "annotate", "sec-1", "job-1", "--add-tag", "远程")
	assert result.exit_code == 0, result.output
	annotated = json.loads(result.output)["data"]["item"]
	assert annotated["tags"] == ["远程"]

	result = _invoke(tmp_path, "remove", "sec-1", "job-1")
	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["data"]["removed"] is True
