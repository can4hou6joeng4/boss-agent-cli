"""CRAWL_PROFILE_BUSY 在 service 与三个命令入口必须可达（Issue #1266 回归）。

`CrawlService._run` 原先只把可选依赖缺失 / 导出失败 / 记录损坏原样抛出；新增的
`CrawlProfileBusy` 是 RuntimeError，落进兜底 `except Exception` 后被记成
status="stopped" 的正常返回，命令层输出 ok=true 的成功信封，`except
CrawlProfileBusy` 永远不可达（真实路径上只会看到「可重试」的 stopped）。
现在它属于确定性异常，service 原样抛出，crawl run / agent crawl / wizard 都
映射成 CRAWL_PROFILE_BUSY。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands import crawl as crawl_module
from boss_agent_cli.crawler.controls import CrawlProfileBusy
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _crawl_profile_busy_error


class _BusyTransport:
	"""open() 立刻撞上「配置目录被另一个采集 Chrome 占用」，不会发出请求。"""

	def open(self):
		raise CrawlProfileBusy("采集配置目录正被运行中的采集 Chrome（pid 123）使用")

	def close(self, *, quit_browser=True):
		pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _settings(tmp_path):
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
		operating_mode="research",
	)


def _invoke(tmp_path, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_service_reraises_profile_busy_instead_of_reporting_stopped(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: _BusyTransport(),
		budget_factory=_NoDelayBudget,
	)
	with cache:
		with pytest.raises(CrawlProfileBusy):
			service.create_and_run(_settings(tmp_path))


def test_crawl_run_reports_profile_busy_envelope(tmp_path, monkeypatch):
	monkeypatch.setattr(
		crawl_module,
		"_seeded_transport_factory",
		lambda ctx: (lambda settings: _BusyTransport()),
	)
	result = _invoke(tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	error = payload["error"]
	assert error["code"] == "CRAWL_PROFILE_BUSY"
	assert error["recoverable"] is True
	assert any("crawl status" in action for action in payload["hints"]["next_actions"])


def test_agent_crawl_reports_profile_busy_envelope(tmp_path, monkeypatch):
	monkeypatch.setattr(
		"boss_agent_cli.commands.agent._seeded_transport_factory",
		lambda ctx: (lambda settings: _BusyTransport()),
	)
	# 简历校验已前移到采集之前（Issue #1407）：先建一份可读简历才能走到 transport。
	init = _invoke(tmp_path, "resume", "init", "--name", "test-resume", "--template", "default")
	assert init.exit_code == 0, init.output
	result = _invoke(
		tmp_path,
		"--role", "recruiter",
		"agent", "crawl", "--query", "AI", "--city", "杭州", "--resume", "test-resume",
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "agent.crawl"
	error = payload["error"]
	assert error["code"] == "CRAWL_PROFILE_BUSY"
	assert error["recoverable"] is True


def test_wizard_maps_profile_busy_to_structured_error(tmp_path):
	context = ActionContext(tmp_path, "zhipin", "candidate", Logger("error"))
	error = _crawl_profile_busy_error(context, CrawlProfileBusy("busy"))

	assert error.code == "CRAWL_PROFILE_BUSY"
	assert error.recoverable is True
	assert "crawl status" in error.recovery_action
