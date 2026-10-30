"""Tests for api/endpoints_loader.py — YAML loading and spec parsing."""

from boss_agent_cli.api.endpoints_loader import (
	load_boss_api_spec, get_spec, EndpointSpec, BossApiSpec,
)


class TestLoadBossApiSpec:
	def test_returns_boss_api_spec(self):
		spec = load_boss_api_spec()
		assert isinstance(spec, BossApiSpec)

	def test_base_url(self):
		spec = load_boss_api_spec()
		assert spec.base_url == "https://www.zhipin.com"

	def test_endpoints_populated(self):
		spec = load_boss_api_spec()
		assert "search" in spec.endpoints
		assert "detail" in spec.endpoints
		assert "greet" in spec.endpoints

	def test_resume_status_endpoint(self):
		spec = load_boss_api_spec()
		assert "resume_status" in spec.endpoints
		ep = spec.endpoints["resume_status"]
		assert ep.method == "GET"
		assert "resume/status" in ep.url

	def test_geek_get_job_endpoint(self):
		spec = load_boss_api_spec()
		assert "geek_get_job" in spec.endpoints
		ep = spec.endpoints["geek_get_job"]
		assert ep.method == "GET"
		assert "geekGetJob" in ep.url

	def test_qr_login_endpoints(self):
		spec = load_boss_api_spec()
		for name in ["qr_randkey", "qr_getqrcode", "qr_scan", "qr_scanlogin", "qr_dispatcher"]:
			assert name in spec.endpoints, f"{name} 端点未定义"
			assert spec.endpoints[name].method == "GET"

	def test_endpoint_spec_fields(self):
		spec = load_boss_api_spec()
		search = spec.endpoints["search"]
		assert isinstance(search, EndpointSpec)
		assert search.name == "search"
		assert search.method == "POST"
		assert "joblist" in search.url

	def test_response_codes(self):
		spec = load_boss_api_spec()
		assert spec.response_codes["success"] == 0
		assert spec.response_codes["stoken_expired"] == 37

	def test_default_headers_include_origin(self):
		spec = load_boss_api_spec()
		assert "Origin" in spec.default_headers
		assert spec.default_headers["Origin"] == "https://www.zhipin.com"

	def test_web_pages_expanded(self):
		spec = load_boss_api_spec()
		assert "geek_base" in spec.web_pages
		assert spec.web_pages["geek_base"].startswith("https://")


class TestGetSpec:
	def test_returns_same_instance(self):
		"""Singleton: get_spec() should return the same object."""
		import boss_agent_cli.api.endpoints_loader as mod
		mod._spec = None  # Reset singleton
		s1 = get_spec()
		s2 = get_spec()
		assert s1 is s2
		mod._spec = None  # Clean up


def test_candidate_referers_use_the_real_jobs_page():
	"""候选者侧 referer 必须指向真实页面 ``/web/geek/jobs``（带 s）。

改前写的是 ``/web/geek/job``：抓包显示浏览器实际停在 ``/web/geek/jobs?query=…``，
真实请求的 Referer 也是它。``web_pages.geek_job`` 当年跟着一起写错，所以两边一致、
看不出来；这里直接钉住路径本身。

不对全部 referer 做白名单断言：招聘者侧与扫码相关端点确实会引用
``/web/frame/recommend/``、``/web/user/`` 这类不在 web_pages 里的页面，
维护一份例外清单只会变成噪音。
"""
	spec = load_boss_api_spec()

	assert spec.web_pages["geek_job"].endswith("/web/geek/jobs")
	for name in ("search", "detail", "recommend"):
		referer = spec.endpoints[name].referer
		assert "/web/geek/job" not in referer.rstrip("s") or referer.endswith("/web/geek/jobs"), name
	for name, endpoint in spec.endpoints.items():
		assert not endpoint.referer.endswith("/web/geek/job"), f"{name} 的 referer 少了 s"
