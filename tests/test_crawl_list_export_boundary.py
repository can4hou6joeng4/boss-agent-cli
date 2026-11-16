"""crawl 翻页终止、导出替换失败与 profile_path 断点校验（Issue #1345/#1346/#1347）。

三个边界都要求「坏输入按确定性错误暴露，而不是静默成功」：
1. 平台 `hasMore` 为 null / 缺失 / 非布尔时不能当成「没有更多」，否则第一页
   就停止且 run 报 completed。
2. 导出目标已是目录时 `os.replace` 失败必须抛 ``CrawlExportError`` 并清理
   ``.jobs.*`` 临时文件，不能落成 ok:true 的 stopped。
3. 断点里的 ``profile_path`` 为空串 / 数字 / 相对路径时，`Path("")` 会变成当前
   目录，resume 会在 cwd 删锁并写 Chrome 配置；必须按记录损坏拒绝。
"""

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings

_MISSING = object()


def _job(job_id: str) -> dict:
	return {"encryptJobId": job_id, "securityId": f"sec-{job_id}", "jobName": "AI 工程师"}


def _page(*jobs: dict, has_more: Any = _MISSING) -> dict:
	data: dict[str, Any] = {"jobList": list(jobs)}
	if has_more is not _MISSING:
		data["hasMore"] = has_more
	return {"code": 0, "zpData": data}


class _FakeTransport:
	def __init__(self, pages: dict[int, dict]) -> None:
		self.pages = pages
		self.page_calls: list[int] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		return self.pages[page_no]

	def fetch_detail(self, security_id: str) -> dict:
		return {"code": 0, "zpData": {"jobCard": {}}}

	def close(self) -> None:
		pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		self._cache.put_crawl_budget(f"test:{kind}", 1.0)


def _settings(tmp_path, *, pages: int = 3) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=pages,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		operating_mode="research",
	)


def _service(tmp_path, transport: _FakeTransport) -> tuple[CacheStore, CrawlService]:
	cache = CacheStore(tmp_path / "cache.db")
	return cache, CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=lambda store: _NoDelayBudget(store),
	)


# ── hasMore 终止语义 ─────────────────────────────────────────────────


def test_null_has_more_pages_to_limit(tmp_path):
	transport = _FakeTransport({
		1: _page(_job("job-1"), has_more=None),
		2: _page(_job("job-2"), has_more=None),
		3: _page(_job("job-3"), has_more=None),
	})
	cache, service = _service(tmp_path, transport)

	outcome = service.create_and_run(_settings(tmp_path, pages=3))

	assert transport.page_calls == [1, 2, 3]
	assert outcome.status == "completed"
	assert outcome.next_page == 4
	assert cache.get_crawl_run(outcome.run_id)["list_finished"] is False


def test_missing_has_more_pages_to_limit(tmp_path):
	"""缺字段本来就是继续翻页；修复不能把默认值改成结束。"""
	transport = _FakeTransport({
		1: _page(_job("job-1")),
		2: _page(_job("job-2")),
		3: _page(_job("job-3")),
	})
	cache, service = _service(tmp_path, transport)

	outcome = service.create_and_run(_settings(tmp_path, pages=3))

	assert transport.page_calls == [1, 2, 3]
	assert outcome.status == "completed"
	assert outcome.next_page == 4
	assert cache.get_crawl_run(outcome.run_id)["list_finished"] is False


@pytest.mark.parametrize("has_more", ["false", 0, 1, 0.0, [], {}])
def test_non_boolean_has_more_pages_to_limit(tmp_path, has_more):
	"""只有显式布尔 false 才是结束；其他类型漂移一律继续到页数上限。"""
	transport = _FakeTransport({
		1: _page(_job("job-1"), has_more=has_more),
		2: _page(_job("job-2"), has_more=has_more),
		3: _page(_job("job-3"), has_more=has_more),
	})
	cache, service = _service(tmp_path, transport)

	outcome = service.create_and_run(_settings(tmp_path, pages=3))

	assert transport.page_calls == [1, 2, 3]
	assert outcome.status == "completed"
	assert cache.get_crawl_run(outcome.run_id)["list_finished"] is False


def test_explicit_false_has_more_stops_after_first_page(tmp_path):
	transport = _FakeTransport({
		1: _page(_job("job-1"), has_more=False),
		2: _page(_job("job-2"), has_more=False),
	})
	cache, service = _service(tmp_path, transport)

	outcome = service.create_and_run(_settings(tmp_path, pages=3))

	assert transport.page_calls == [1]
	assert outcome.status == "completed"
	assert outcome.next_page == 2
	assert cache.get_crawl_run(outcome.run_id)["list_finished"] is True


# ── 导出替换失败 ─────────────────────────────────────────────────────


def test_export_output_directory_blocked_by_a_file_raises_export_error(tmp_path):
	"""mkdir 在异常转换之外时原始 FileExistsError 会被 _run 兜成 ok:true 的 stopped（Issue #1409）。"""
	output_dir = tmp_path / "blocked"
	output_dir.write_text("not a directory", encoding="utf-8")

	with pytest.raises(CrawlExportError) as excinfo:
		write_run_outputs(output_dir, [{"title": "AI"}])

	assert str(output_dir) in str(excinfo.value)


def test_export_target_directory_raises_and_cleans_temporaries(tmp_path):
	(tmp_path / "jobs.xlsx").mkdir()

	with pytest.raises(CrawlExportError) as excinfo:
		write_run_outputs(tmp_path, [{"title": "AI"}])

	message = str(excinfo.value)
	assert "jobs.xlsx" in message
	assert "IsADirectoryError" in message
	assert sorted(path.name for path in tmp_path.iterdir()) == ["jobs.csv", "jobs.json", "jobs.xlsx"]
	assert not any(path.name.startswith(".jobs.") for path in tmp_path.iterdir())


def test_service_propagates_export_error_instead_of_ok_stopped(tmp_path):
	"""导出失败必须走确定性异常元组，而不是被 _run 吞成 ok:true 的 stopped。"""
	transport = _FakeTransport({1: _page(_job("job-1"), has_more=False)})
	cache, service = _service(tmp_path, transport)
	run_id = service.create(_settings(tmp_path, pages=1))
	output_dir = tmp_path / "crawl" / "runs" / run_id
	(output_dir / "jobs.xlsx").mkdir(parents=True)

	with pytest.raises(CrawlExportError):
		service.resume(run_id)

	assert cache.get_crawl_run(run_id)["status"] == "stopped"
	assert not any(path.name.startswith(".jobs.") for path in output_dir.iterdir())


# ── profile_path 断点校验 ────────────────────────────────────────────


def _params(tmp_path, **overrides):
	params = {
		"query": "python",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	params.update(overrides)
	return params


def _browser(tmp_path, profile_path: Any) -> dict:
	return {"profile_path": profile_path, "cdp_port": 9333}


@pytest.mark.parametrize(
	("profile_path", "expected_type"),
	[
		("", "str"),
		("   ", "str"),
		(123, "int"),
		({"path": "/tmp/profile"}, "dict"),
		("profiles/x", "str"),
	],
)
def test_from_dict_rejects_profile_path_not_non_empty_absolute(tmp_path, profile_path, expected_type):
	params = _params(tmp_path, browser=_browser(tmp_path, profile_path))

	with pytest.raises(CacheRecordInvalid) as excinfo:
		CrawlSettings.from_dict(params, run_id="run-1")

	message = str(excinfo.value)
	assert "params.browser.profile_path" in message
	assert expected_type in message


def test_from_dict_accepts_absolute_profile_path(tmp_path):
	params = _params(tmp_path, browser=_browser(tmp_path, str(tmp_path / "profile")))

	settings = CrawlSettings.from_dict(params, run_id="run-1")

	assert settings.profile_path == tmp_path / "profile"


def test_relative_data_dir_stores_absolute_profile_path(tmp_path, monkeypatch):
	"""--data-dir 为相对路径时，断点里仍必须落绝对 profile 路径。

	`main._resolve_data_dir` 只做 expanduser，不做绝对化：`--data-dir state`
	会让 `state/crawl/chrome-profile` 以相对形式进断点，之后在不同 cwd 下
	resume 会指向另一个目录，也会被 from_dict 的绝对路径校验判成记录损坏。
	"""
	from unittest.mock import MagicMock

	from boss_agent_cli.commands.crawl import _settings_from_context

	monkeypatch.chdir(tmp_path)
	ctx = MagicMock()
	ctx.obj = {
		"data_dir": Path("state"),
		"config": {"crawl": {"cdp_port": 9333}},
		"platform": "zhipin",
		"role": "candidate",
	}
	settings = _settings_from_context(
		ctx,
		query="AI",
		city="杭州",
		pages=1,
		with_detail=False,
		hook_profile=None,
		hook_dir=None,
		profile_path=None,
		chrome_path=None,
		cdp_port=9333,
	)
	assert settings.profile_path.is_absolute()
	assert settings.profile_path == (tmp_path / "state" / "crawl" / "chrome-profile").resolve()
