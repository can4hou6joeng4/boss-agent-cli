import pytest

from boss_agent_cli.search_filters import SearchFilterCriteria, SearchPipelinePlatformError, resolve_welfare_keywords, run_search_pipeline


class FakeLogger:
	def __init__(self):
		self.messages: list[str] = []

	def info(self, message: str):
		self.messages.append(message)


class FakeCache:
	def __init__(self, greeted_ids: set[str] | None = None):
		self.greeted_ids = greeted_ids or set()

	def is_greeted(self, security_id: str) -> bool:
		return security_id in self.greeted_ids


class FakeClient:
	def __init__(self, pages: list[dict], descriptions: dict[str, str | Exception]):
		self.pages = list(pages)
		self.descriptions = descriptions
		self.search_calls: list[dict] = []
		self.detail_calls: list[tuple[str, str]] = []

	def search_jobs(self, query: str, **filters):
		self.search_calls.append({"query": query, "filters": filters})
		return self.pages.pop(0)

	def is_success(self, response: dict) -> bool:
		return response.get("code", 0) in (0, 200)

	def parse_error(self, response: dict) -> tuple[str, str]:
		return response.get("error_code", "UNKNOWN"), response.get("message", "")

	def job_card(self, security_id: str, lid: str = ""):
		self.detail_calls.append((security_id, lid))
		value = self.descriptions[security_id]
		if isinstance(value, Exception):
			raise value
		return {"zpData": {"jobCard": {"postDescription": value}}}


def _make_job_raw(*, security_id: str, job_id: str, welfare: list[str] | None = None, lid: str = ""):
	return {
		"encryptJobId": job_id,
		"jobName": f"Job-{job_id}",
		"brandName": f"Company-{job_id}",
		"salaryDesc": "20-30K",
		"cityName": "广州",
		"areaDistrict": "天河区",
		"jobExperience": "3-5年",
		"jobDegree": "本科",
		"skills": ["Python"],
		"welfareList": welfare or [],
		"brandIndustry": "互联网",
		"brandScaleName": "100-499人",
		"brandStageName": "A轮",
		"bossName": "李女士",
		"bossTitle": "HR",
		"bossOnline": True,
		"securityId": security_id,
		"lid": lid,
	}


def _welfare_conditions():
	return [("双休", resolve_welfare_keywords("双休"))]


def test_pipeline_uses_detail_fallback_and_marks_greeted():
	client = FakeClient(
		pages=[{"zpData": {"hasMore": False, "jobList": [_make_job_raw(security_id="sec-1", job_id="job-1")]}}],
		descriptions={"sec-1": "这里提供双休和五险一金"},
	)
	cache = FakeCache({"sec-1"})
	logger = FakeLogger()

	result = run_search_pipeline(
		client,
		cache,
		logger,
		criteria=SearchFilterCriteria(query="python"),
		welfare_conditions=_welfare_conditions(),
	)

	assert len(result.items) == 1
	assert result.items[0]["security_id"] == "sec-1"
	assert result.items[0]["greeted"] is True
	assert "双休(描述)" in result.items[0]["welfare_match"]


def test_pipeline_detail_exception_does_not_abort_other_matches():
	client = FakeClient(
		pages=[
			{
				"zpData": {
					"hasMore": False,
					"jobList": [
						_make_job_raw(security_id="sec-fail", job_id="job-fail"),
						_make_job_raw(security_id="sec-ok", job_id="job-ok"),
					],
				},
			},
		],
		descriptions={
			"sec-fail": OSError("network error"),
			"sec-ok": "岗位描述明确写了双休",
		},
	)

	result = run_search_pipeline(
		client,
		FakeCache(),
		FakeLogger(),
		criteria=SearchFilterCriteria(query="python"),
		welfare_conditions=_welfare_conditions(),
	)

	assert [item["security_id"] for item in result.items] == ["sec-ok"]


def test_pipeline_skip_greeted_filters_detail_matched_items():
	client = FakeClient(
		pages=[{"zpData": {"hasMore": False, "jobList": [_make_job_raw(security_id="sec-1", job_id="job-1")]}}],
		descriptions={"sec-1": "这里提供双休"},
	)

	result = run_search_pipeline(
		client,
		FakeCache({"sec-1"}),
		FakeLogger(),
		criteria=SearchFilterCriteria(query="python"),
		welfare_conditions=_welfare_conditions(),
		skip_greeted=True,
	)

	assert result.items == []


def test_pipeline_stops_after_limit_during_welfare_search():
	client = FakeClient(
		pages=[
			{
				"zpData": {
					"hasMore": True,
					"jobList": [
						_make_job_raw(security_id="sec-1", job_id="job-1", welfare=["双休"]),
						_make_job_raw(security_id="sec-2", job_id="job-2", welfare=["双休"]),
					],
				},
			},
			{
				"zpData": {
					"hasMore": False,
					"jobList": [_make_job_raw(security_id="sec-3", job_id="job-3", welfare=["双休"])],
				},
			},
		],
		descriptions={},
	)

	result = run_search_pipeline(
		client,
		FakeCache(),
		FakeLogger(),
		criteria=SearchFilterCriteria(query="python"),
		welfare_conditions=_welfare_conditions(),
		limit=1,
		max_pages=5,
	)

	assert len(result.items) == 1
	assert len(client.search_calls) == 1
	assert result.has_more is True


def test_pipeline_respects_max_pages_even_when_has_more():
	client = FakeClient(
		pages=[
			{
				"zpData": {
					"hasMore": True,
					"jobList": [_make_job_raw(security_id="sec-1", job_id="job-1", welfare=["双休"])],
				},
			},
			{
				"zpData": {
					"hasMore": True,
					"jobList": [_make_job_raw(security_id="sec-2", job_id="job-2", welfare=["双休"])],
				},
			},
		],
		descriptions={},
	)

	result = run_search_pipeline(
		client,
		FakeCache(),
		FakeLogger(),
		criteria=SearchFilterCriteria(query="python"),
		welfare_conditions=_welfare_conditions(),
		max_pages=1,
	)

	assert len(client.search_calls) == 1
	assert len(result.items) == 1
	assert result.last_page == 1
	assert result.has_more is True


def test_pipeline_reports_platform_error():
	client = FakeClient(
		pages=[{"code": 500, "message": "service unavailable", "error_code": "UPSTREAM_ERROR"}],
		descriptions={},
	)

	with pytest.raises(SearchPipelinePlatformError) as exc_info:
		run_search_pipeline(
			client,
			FakeCache(),
			FakeLogger(),
			criteria=SearchFilterCriteria(query="python"),
		)

	assert exc_info.value.code == "UPSTREAM_ERROR"
	assert exc_info.value.message == "service unavailable"
