import pytest

from boss_agent_cli.api.egress import (
	EgressContradictionError,
	ensure_egress_consistent,
	evaluate_egress,
	language_region,
	probe_egress,
	timezone_region,
)


def test_language_region_reads_first_tag_only() -> None:
	assert language_region("zh-CN,zh;q=0.9") == "CN"
	assert language_region("ja-JP") == "JP"
	assert language_region("zh") == ""
	assert language_region("") == ""


def test_timezone_region_maps_known_prefixes() -> None:
	assert timezone_region("Asia/Shanghai") == "CN"
	assert timezone_region("Asia/Tokyo") == "JP"
	assert timezone_region("America/New_York") == "US"
	assert timezone_region("Pacific/Auckland") == ""


def test_matching_egress_and_locale_is_ok() -> None:
	verdict = evaluate_egress(exit_country="CN", accept_language="zh-CN,zh;q=0.9", timezone="Asia/Shanghai")

	assert verdict.status == "ok"
	assert not verdict.refused


def test_foreign_exit_with_chinese_locale_is_a_mismatch() -> None:
	verdict = evaluate_egress(exit_country="JP", accept_language="zh-CN,zh;q=0.9", timezone="Asia/Shanghai")

	assert verdict.status == "mismatch"
	assert verdict.refused
	assert len(verdict.contradictions) == 2


def test_timezone_alone_can_contradict_the_exit() -> None:
	verdict = evaluate_egress(exit_country="CN", accept_language="", timezone="Asia/Tokyo")

	assert verdict.status == "mismatch"
	assert "时区" in verdict.contradictions[0]


def test_unknown_exit_or_unlisted_region_never_refuses() -> None:
	assert evaluate_egress(exit_country="", accept_language="zh-CN", timezone="Asia/Shanghai").status == "unknown"
	assert evaluate_egress(exit_country="ZZ", accept_language="zh-CN", timezone="Asia/Shanghai").status == "unknown"


def test_unknown_locale_parts_are_not_treated_as_contradiction() -> None:
	verdict = evaluate_egress(exit_country="CN", accept_language="zh", timezone="")

	assert verdict.status == "ok"


def test_ensure_egress_consistent_raises_on_mismatch() -> None:
	with pytest.raises(EgressContradictionError) as caught:
		ensure_egress_consistent(exit_country="US", accept_language="ja-JP", timezone="Asia/Tokyo")

	assert caught.value.verdict.status == "mismatch"
	assert ensure_egress_consistent(
		exit_country="CN", accept_language="zh-CN", timezone="Asia/Shanghai"
	).status == "ok"


class _Response:
	def __init__(self, payload):
		self._payload = payload

	def json(self):
		return self._payload


class _Client:
	def __init__(self, payload):
		self._payload = payload
		self.requested: list[str] = []
		self.closed = False

	def get(self, url):
		self.requested.append(url)
		return _Response(self._payload)

	def close(self):
		self.closed = True


def test_probe_is_inert_without_configured_url() -> None:
	client = _Client({"country": "JP"})

	verdict = probe_egress(environ={}, client=client)

	assert verdict.status == "unknown"
	assert client.requested == []


def test_probe_reads_country_from_configured_endpoint() -> None:
	client = _Client({"country": "JP"})

	verdict = probe_egress(environ={"BOSS_EGRESS_PROBE_URL": "https://probe.test/json"}, client=client)

	assert client.requested == ["https://probe.test/json"]
	assert verdict.exit_country == "JP"


def test_probe_reports_unknown_on_transport_failure() -> None:
	class _Boom(_Client):
		def get(self, url):
			raise RuntimeError("network down")

	verdict = probe_egress(environ={"BOSS_EGRESS_PROBE_URL": "https://probe.test/json"}, client=_Boom({}))

	assert verdict.status == "unknown"
	assert "network down" not in verdict.detail


def test_crawl_service_refuses_when_probe_contradicts(monkeypatch, tmp_path) -> None:
	"""配置了出口探测且探测结果与声明矛盾时，create() 必须拒绝开跑。"""
	from boss_agent_cli.api.egress import EgressContradictionError
	from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
	from boss_agent_cli.cache.store import CacheStore

	monkeypatch.setenv("BOSS_EGRESS_PROBE_URL", "https://probe.test/json")
	monkeypatch.setattr(
		"boss_agent_cli.api.egress.probe_egress",
		lambda *a, **k: __import__("boss_agent_cli.api.egress", fromlist=["x"]).EgressVerdict(
			status="unknown", exit_country="JP", detail="probe"
		),
	)

	service = CrawlService(
		CacheStore(tmp_path / "cache.sqlite3"),
		data_dir=tmp_path,
		transport_factory=lambda _settings: None,
	)
	settings = CrawlSettings(
		query="Python",
		city_code="101280100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)

	with pytest.raises(EgressContradictionError):
		service.create(settings)


def test_crawl_service_starts_when_no_probe_configured(monkeypatch, tmp_path) -> None:
	from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
	from boss_agent_cli.cache.store import CacheStore

	monkeypatch.delenv("BOSS_EGRESS_PROBE_URL", raising=False)
	service = CrawlService(
		CacheStore(tmp_path / "cache.sqlite3"),
		data_dir=tmp_path,
		transport_factory=lambda _settings: None,
	)

	assert service.create(
		CrawlSettings(
			query="Python",
			city_code="101280100",
			pages=1,
			with_detail=False,
			profile_path=tmp_path / "profile",
			chrome_path=None,
			cdp_port=9222,
			hook_profile="none",
		)
	)
