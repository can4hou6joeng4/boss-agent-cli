"""ZhilianPlatform 契约测试。"""

from __future__ import annotations

import json
from urllib.parse import urlparse
from unittest.mock import MagicMock
from click.testing import CliRunner

from boss_agent_cli.platforms import BossPlatform, Platform, get_platform, list_platforms
from boss_agent_cli.platforms.zhilian import ZhilianPlatform


class TestZhilianRegistration:
	"""Zhilian 已注册到 Platform 注册表。"""

	def test_list_platforms_contains_zhilian(self) -> None:
		assert "zhilian" in list_platforms()

	def test_list_platforms_still_contains_zhipin(self) -> None:
		assert "zhipin" in list_platforms()

	def test_get_platform_returns_zhilian_class(self) -> None:
		assert get_platform("zhilian") is ZhilianPlatform

	def test_zhilian_subclasses_platform(self) -> None:
		assert issubclass(ZhilianPlatform, Platform)

	def test_zhilian_is_distinct_from_boss(self) -> None:
		assert ZhilianPlatform is not BossPlatform


class TestZhilianMetadata:
	"""Zhilian 基础元信息（对齐 docs/research/platforms/zhaopin.md）。"""

	def setup_method(self) -> None:
		self.plat = ZhilianPlatform(MagicMock())

	def test_name_is_zhilian(self) -> None:
		assert self.plat.name == "zhilian"

	def test_display_name_is_chinese(self) -> None:
		assert self.plat.display_name == "智联招聘"

	def test_base_url_points_to_zhaopin(self) -> None:
		assert urlparse(self.plat.base_url).hostname == "m.zhaopin.com"


class TestZhilianEnvelopeAdapter:
	"""Zhilian 响应包络适配（基于 zhaopin.md §4）。"""

	def setup_method(self) -> None:
		self.plat = ZhilianPlatform(MagicMock())

	def test_is_success_code_200(self) -> None:
		"""智联成功是 code == 200，区别于 BOSS 的 code == 0。"""
		assert self.plat.is_success({"code": 200, "data": {}}) is True

	def test_is_success_non_200(self) -> None:
		assert self.plat.is_success({"code": 401}) is False

	def test_is_success_missing_code(self) -> None:
		assert self.plat.is_success({}) is False

	def test_unwrap_data_from_data_key(self) -> None:
		"""智联数据在 data key，区别于 BOSS 的 zpData。"""
		result = self.plat.unwrap_data({"code": 200, "data": {"list": [1, 2]}})
		assert result == {"list": [1, 2]}

	def test_unwrap_data_missing(self) -> None:
		assert self.plat.unwrap_data({"code": 200}) is None

	def test_parse_error_unauthorized(self) -> None:
		"""智联 401 → AUTH_EXPIRED / AUTH_REQUIRED。"""
		code, _ = self.plat.parse_error({"code": 401, "message": "unauthorized"})
		assert code in ("AUTH_EXPIRED", "AUTH_REQUIRED")

	def test_parse_error_forbidden_as_risk(self) -> None:
		"""智联 403 → ACCOUNT_RISK。"""
		code, _ = self.plat.parse_error({"code": 403, "message": "forbidden"})
		assert code == "ACCOUNT_RISK"

	def test_parse_error_rate_limited(self) -> None:
		"""智联 429 → RATE_LIMITED。"""
		code, _ = self.plat.parse_error({"code": 429, "message": "too many"})
		assert code == "RATE_LIMITED"

	def test_parse_error_unknown(self) -> None:
		code, _ = self.plat.parse_error({"code": 999, "message": "whatever"})
		assert code == "UNKNOWN"

	def test_parse_error_status_code_network_error(self) -> None:
		code, _ = self.plat.parse_error({"status_code": 503, "message": "service unavailable"})
		assert code == "NETWORK_ERROR"

	def test_parse_error_message_auth_expired(self) -> None:
		code, _ = self.plat.parse_error({"code": "ERR", "message": "token expired"})
		assert code == "AUTH_EXPIRED"

	def test_parse_error_message_rate_limited(self) -> None:
		code, _ = self.plat.parse_error({"code": "ERR", "message": "rate limit"})
		assert code == "RATE_LIMITED"

	def test_parse_error_message_account_risk(self) -> None:
		code, _ = self.plat.parse_error({"code": "ERR", "message": "risk control"})
		assert code == "ACCOUNT_RISK"


class TestZhilianDelegation:
	"""ZhilianPlatform 只读方法委托给底层 ZhilianClient。"""

	def setup_method(self) -> None:
		self.mock_client = MagicMock()
		self.plat = ZhilianPlatform(self.mock_client)

	def test_search_jobs_delegates(self) -> None:
		self.mock_client.search_jobs.return_value = {"code": 200, "data": {}}
		result = self.plat.search_jobs("Python", city="530")
		self.mock_client.search_jobs.assert_called_once_with("Python", city="530")
		assert result == {"code": 200, "data": {}}

	def test_job_detail_delegates(self) -> None:
		self.mock_client.job_detail.return_value = {"code": 200, "data": {}}
		self.plat.job_detail("job-1")
		self.mock_client.job_detail.assert_called_once_with("job-1")

	def test_recommend_jobs_delegates(self) -> None:
		self.mock_client.recommend_jobs.return_value = {"code": 200, "data": {}}
		self.plat.recommend_jobs(page=2)
		self.mock_client.recommend_jobs.assert_called_once_with(2)

	def test_user_info_delegates(self) -> None:
		self.mock_client.user_info.return_value = {"code": 200, "data": {}}
		self.plat.user_info()
		self.mock_client.user_info.assert_called_once_with()

	def test_greet_delegates(self) -> None:
		self.mock_client.greet.return_value = {"code": 200, "data": {}}
		self.plat.greet("sid", "jid", "hi")
		self.mock_client.greet.assert_called_once_with("sid", "jid", "hi")

	def test_apply_delegates(self) -> None:
		self.mock_client.apply.return_value = {"code": 200, "data": {}}
		self.plat.apply("sid", "jid", "lid-1")
		self.mock_client.apply.assert_called_once_with("sid", "jid", "lid-1")

	def test_job_card_delegates(self) -> None:
		self.mock_client.job_card.return_value = {"code": 200, "data": {}}
		self.plat.job_card("sid", "lid-1")
		self.mock_client.job_card.assert_called_once_with("sid", "lid-1")

	def test_job_history_delegates(self) -> None:
		self.mock_client.job_history.return_value = {"code": 200, "data": {}}
		self.plat.job_history(page=3)
		self.mock_client.job_history.assert_called_once_with(3)

	def test_resume_baseinfo_delegates(self) -> None:
		self.mock_client.resume_baseinfo.return_value = {"code": 200, "data": {}}
		self.plat.resume_baseinfo()
		self.mock_client.resume_baseinfo.assert_called_once_with()

	def test_resume_expect_delegates(self) -> None:
		self.mock_client.resume_expect.return_value = {"code": 200, "data": {}}
		self.plat.resume_expect()
		self.mock_client.resume_expect.assert_called_once_with()

	def test_deliver_list_delegates(self) -> None:
		self.mock_client.deliver_list.return_value = {"code": 200, "data": {}}
		self.plat.deliver_list(page=4)
		self.mock_client.deliver_list.assert_called_once_with(page=4)

	def test_social_personal_data_methods_stay_unimplemented(self) -> None:
		for method_name, args in [
			("chat_history", ("gid-1", "sid")),
			("friend_list", ()),
			("friend_label", ("friend-1", 1)),
			("exchange_contact", ("sid", "uid", "name")),
		]:
			try:
				getattr(self.plat, method_name)(*args)
			except NotImplementedError as exc:
				assert "zhilian" in str(exc)
			else:
				raise AssertionError(f"{method_name} should remain unimplemented for zhilian")

	def test_platform_can_enter_with_context(self) -> None:
		mock_client = MagicMock()
		plat = ZhilianPlatform(mock_client)
		with plat:
			pass
		mock_client.close.assert_called_once()


class TestZhilianCliIntegration:
	"""CLI 集成：--platform zhilian schema 可用且暴露正确字段。"""

	def test_zhilian_accepted_as_platform_option(self) -> None:
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["--platform", "zhilian", "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert payload["data"]["current_platform"] == "zhilian"

	def test_schema_supported_platforms_includes_zhilian(self) -> None:
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert "zhilian" in payload["data"]["supported_platforms"]
		assert "zhipin-recruiter" in payload["data"]["supported_recruiter_platforms"]

	def test_schema_platform_choice_updated(self) -> None:
		"""schema 的 --platform 选项 choices 应包含 zhilian。"""
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		platform_opt = payload["data"]["global_options"]["--platform"]
		assert "zhilian" in platform_opt.get("choices", [])

	def test_schema_marks_hr_as_recruiter_only(self) -> None:
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		availability = payload["data"]["commands"]["hr"]["availability"]
		assert availability["roles"] == ["recruiter"]
		assert availability["candidate_platforms"] == []
