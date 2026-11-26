"""采集导入不得把纯空白职位标识写入候选池（Issue #2984）。"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


def _seed(data_dir: Path, payloads: list[dict]) -> None:
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run_demo", {"query": "示例"}, str(data_dir / "out"))
		for index, payload in enumerate(payloads):
			cache.put_crawl_job("run_demo", f"job_key_{index}", 1, payload, detail_done=False)


@pytest.mark.parametrize("field", ["security_id", "job_id"])
@pytest.mark.parametrize("blank", [" ", "\t", "\n", "　"])
def test_crawl_import_skips_whitespace_identifiers(tmp_path: Path, field: str, blank: str) -> None:
	invalid = {"security_id": "sec_invalid", "job_id": "job_invalid", "title": "无效示例"}
	invalid[field] = blank
	_seed(tmp_path, [invalid, {"security_id": "sec_demo", "job_id": "job_demo", "title": "有效示例"}])
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "shortlist", "run_demo", "--all"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)["data"]
	assert payload["selected_count"] == 2
	assert payload["imported_count"] == 1
	assert payload["existing_count"] == 0
	assert payload["skipped_count"] == 1
	assert [entry["title"] for entry in payload["imported"]] == ["有效示例"]
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert [row["job_id"] for row in cache.list_shortlist()] == ["job_demo"]


def test_crawl_import_keeps_valid_identifiers_and_dedupe(tmp_path: Path) -> None:
	_seed(tmp_path, [
		{"security_id": " ", "job_id": "job_demo", "title": "空白访问标识"},
		{"security_id": "sec_v1", "job_id": "job_demo", "title": "正常职位"},
	])
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "shortlist", "run_demo", "--all"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)["data"]
	assert payload["imported_count"] == 1
	assert payload["skipped_count"] == 1
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		rows = cache.list_shortlist()
	assert [row["security_id"] for row in rows] == ["sec_v1"]


def test_crawl_import_blank_ids_do_not_shadow_existing_shortlist(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run_demo", {"query": "示例"}, str(tmp_path / "out"))
		cache.add_shortlist({"security_id": "sec_existing", "job_id": "job_demo", "title": "既有职位"})
		cache.put_crawl_job("run_demo", "job_key_blank", 1, {
			"security_id": " ", "job_id": "job_demo", "title": "空白访问标识",
		}, detail_done=False)
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "shortlist", "run_demo", "--all"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)["data"]
	assert payload["imported_count"] == 0
	assert payload["skipped_count"] == 1
	assert payload["existing_count"] == 0
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		rows = cache.list_shortlist()
	assert [(row["security_id"], row["title"]) for row in rows] == [("sec_existing", "既有职位")]


def test_crawl_import_none_identifiers_still_skipped(tmp_path: Path) -> None:
	_seed(tmp_path, [{"security_id": None, "job_id": "job_demo", "title": "空访问标识"}])
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "shortlist", "run_demo", "--all"])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)["data"]
	assert payload["imported_count"] == 0
	assert payload["skipped_count"] == 1
