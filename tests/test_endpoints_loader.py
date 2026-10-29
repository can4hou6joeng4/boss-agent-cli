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
