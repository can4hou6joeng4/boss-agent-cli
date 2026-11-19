"""搜索筛选与本地 JSON 写回的边界（Issue #1889 #1890 #1891）。"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	parse_salary_range,
	run_search_pipeline,
)
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic


def _deep_dict(depth: int) -> dict:
	value: dict = {}
	for _ in range(depth):
		value = {"a": value}
	return value


class _QuietLogger:
	def info(self, message: str) -> None:
		pass


def _client(raw_item: dict, *, job_card: dict | None = None):
	class _Client:
		name = "zhipin"

		def search_jobs(self, query: str, **_kwargs):
			return {"code": 0, "zpData": {"jobList": [raw_item], "hasMore": False}}

		def is_success(self, raw: dict) -> bool:
			return True

		def parse_error(self, raw: dict):
			return ("PLATFORM_ERROR", "")

		def job_card(self, security_id: str, lid: str):
			return {"code": 0, "zpData": {"jobCard": job_card or {}}}

		def unwrap_data(self, raw: dict) -> dict:
			return raw["zpData"]

		def is_browser_only(self) -> bool:
			return False

	return _Client()


# ── Issue #1889：超长 salaryDesc 不崩整条搜索 ────────────────────────


def test_超长数字_salary_desc_按无法解析处理() -> None:
	assert parse_salary_range("1" * 4301 + "K") is None
	assert parse_salary_range("1" * 4301 + "-" + "2" * 4301 + "K") is None


def test_正常薪资区间保持不变() -> None:
	assert parse_salary_range("20-50K") == (20, 50)
	assert parse_salary_range("30K") == (30, 30)
	assert parse_salary_range("面议") is None


# ── Issue #1890：encryptJobId 漂移不穿出 --welfare 管线 ─────────────


@pytest.mark.parametrize("drifted", [{"bad": 1}, ["bad"], 123, None])
def test_描述缓存对漂移键按未命中处理(tmp_path, drifted: object) -> None:
	with CacheStore(tmp_path / "c.db") as store:
		assert store.get_job_desc(drifted) is None  # type: ignore[arg-type]
		store.put_job_desc(drifted, "描述")  # type: ignore[arg-type]
		assert store.get_job_desc("job-1") is None


def test_描述缓存正常读写() -> None:
	import tempfile

	with tempfile.TemporaryDirectory() as data_dir, CacheStore(Path(data_dir) / "c.db") as store:
		store.put_job_desc("job-1", "双休")
		assert store.get_job_desc("job-1") == "双休"
		store.put_job_desc("job-1", {"bad": 1})  # type: ignore[arg-type]
		assert store.get_job_desc("job-1") == "双休"


def test_welfare_管线在_encrypt_job_id_漂移时仍返回结果(tmp_path) -> None:
	item = {
		"jobName": "后端",
		"brandName": "TestCo",
		"encryptJobId": {"bad": 1},
		"securityId": "s1",
		"lid": "l1",
		"bossOnline": True,
		"welfareList": [],
	}

	with CacheStore(tmp_path / "c.db") as store:
		result = run_search_pipeline(
			_client(item, job_card={"postDescription": "双休", "activeTimeDesc": "今日活跃"}),
			store,
			_QuietLogger(),
			criteria=SearchFilterCriteria(query="q"),
			welfare_conditions=[("双休", ["双休"])],
			max_pages=1,
		)

	assert len(result.items) == 1


# ── Issue #1891：深嵌套 / 超长整数写回归为本地 IO 错误 ──────────────


def test_深嵌套写回抛_OSError_而不是_RecursionError(tmp_path) -> None:
	path = tmp_path / "state.json"
	path.write_text('{"old": true}', encoding="utf-8")

	with pytest.raises(OSError) as excinfo:
		write_json_atomic(path, _deep_dict(2000))

	assert excinfo.value.errno == errno.EINVAL
	assert path.read_text(encoding="utf-8") == '{"old": true}'


def test_超长整数写回抛_OSError(tmp_path) -> None:
	with pytest.raises(OSError) as excinfo:
		write_json_atomic(tmp_path / "state.json", {"n": 10**5000})

	assert excinfo.value.errno == errno.EINVAL


def test_strict_json_text_深嵌套抛_OSError() -> None:
	with pytest.raises(OSError):
		strict_json_text(_deep_dict(2000))


def test_strict_json_text_正常序列化() -> None:
	assert json.loads(strict_json_text({"n": 1})) == {"n": 1}
