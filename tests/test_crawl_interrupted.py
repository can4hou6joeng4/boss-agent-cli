"""未完成的采集收到退出信号时保存可恢复断点，再原样传播信号。"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings


class _ExecutorGone(BaseException):
	pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind):
		pass


class _Transport:
	def __init__(self, phase=None, interruption=None):
		self.phase = phase
		self.interruption = interruption
		self.pages = []
		self.details = []
		self.closed = False

	def _interrupt(self, phase):
		if self.phase == phase:
			raise self.interruption

	def open(self):
		self._interrupt("open")
		return []

	def fetch_page(self, query, city, page):
		self.pages.append(page)
		if page == 2:
			self._interrupt("list")
		return {"code": 0, "zpData": {"jobList": [{
			"encryptJobId": f"job-{page}", "securityId": f"fixture-sec-{page}",
			"jobName": "测试职位", "brandName": "测试公司",
		}], "hasMore": page < 2}}

	def fetch_detail(self, security_id):
		self.details.append(security_id)
		self._interrupt("detail")
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "测试描述"}}}

	def close(self, *, quit_browser=True):
		self.closed = True


def _service(cache, data_dir, transport):
	return CrawlService(
		cache, data_dir=data_dir, transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)


@pytest.mark.parametrize("exception_type", [SystemExit, KeyboardInterrupt, GeneratorExit, _ExecutorGone])
@pytest.mark.parametrize("phase", ["open", "list", "detail"])
def test_interruption_preserves_progress_and_resumes_only_unfinished_work(tmp_path, exception_type, phase):
	interruption = exception_type(23) if exception_type is SystemExit else exception_type("fixture private context")
	first = _Transport(phase, interruption)
	second = _Transport()
	settings = CrawlSettings(
		query="fixture", city_code="101210100", pages=2, with_detail=phase == "detail",
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9333, hook_profile="none",
	)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		runner = _service(cache, tmp_path, first)
		run_id = runner.create(settings)
		with pytest.raises(exception_type) as raised:
			runner.resume(run_id)
		assert raised.value is interruption
		assert first.closed is True
		run = cache.get_crawl_run(run_id)
		assert run["status"] == "stopped"
		assert run["list_finished"] is False
		assert run["next_page"] == (1 if phase == "open" else 2)
		assert run["requests_attempted"] == (0 if phase == "open" else 2)
		assert run["detail_requests_attempted"] == (1 if phase == "detail" else 0)
		assert run["error"]
		assert "fixture private context" not in run["error"]
		jobs = cache.list_crawl_jobs(run_id)
		assert [job["job_key"] for job in jobs] == ([] if phase == "open" else ["job-1"])
		if phase == "detail":
			assert jobs[0]["detail_done"] is False
		# 同一个任务实际续跑；只断言 completed 会让未完成任务提前返回的缺陷蒙混过关。
		outcome = _service(cache, tmp_path, second).resume(run_id)
		assert outcome.status == "completed"
		assert second.pages == ([1, 2] if phase == "open" else [2])
		assert second.details == (["fixture-sec-1", "fixture-sec-2"] if phase == "detail" else [])
		assert outcome.requests_attempted == {"open": 2, "list": 3, "detail": 5}[phase]
		assert outcome.jobs_seen == 2
		assert outcome.next_page == 3
		assert cache.get_crawl_run(run_id)["error"] == ""
	assert list((tmp_path / "cache" / "crawl-locks").glob("*.lock")) == []


def test_system_exit_keeps_the_process_exit_code_and_a_resumable_checkpoint(tmp_path):
	settings = CrawlSettings(
		query="fixture", city_code="101210100", pages=2, with_detail=False,
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9333, hook_profile="none",
	)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run_id = _service(cache, tmp_path, _Transport()).create(settings)
	child = subprocess.run([
		sys.executable, "-c", textwrap.dedent("""\
import sys
from pathlib import Path
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService
class NoDelay(CrawlBudget):
    def wait(self, kind):
        pass
class Exiting:
    def open(self):
        return []
    def fetch_page(self, *args):
        sys.exit(23)
    def close(self, **kwargs):
        pass
data_dir = Path(sys.argv[1])
with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
    CrawlService(cache, data_dir=data_dir, transport_factory=lambda settings: Exiting(), budget_factory=NoDelay).resume(sys.argv[2])
"""), str(tmp_path), run_id,
	], capture_output=True, text=True, timeout=30)
	assert child.returncode == 23, child.stderr
	transport = _Transport()
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run(run_id)
		assert run["status"] == "stopped"
		assert run["next_page"] == 1
		assert run["requests_attempted"] == 1
		assert _service(cache, tmp_path, transport).resume(run_id).status == "completed"
		assert transport.pages == [1, 2]
