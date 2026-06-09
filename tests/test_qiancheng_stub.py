"""51job/QianchengPlatform 占位适配器契约测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from urllib.parse import urlparse

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.platforms import Platform, get_platform, list_platforms
from boss_agent_cli.platforms.qiancheng import QianchengPlatform


class TestQianchengRegistration:
	def test_list_platforms_contains_aliases(self) -> None:
		platforms = list_platforms()
		assert "qiancheng" in platforms
		assert "51job" in platforms

	def test_get_platform_returns_qiancheng_class(self) -> None:
		assert get_platform("qiancheng") is QianchengPlatform
		assert get_platform("51job") is QianchengPlatform

	def test_qiancheng_subclasses_platform(self) -> None:
		assert issubclass(QianchengPlatform, Platform)


class TestQianchengMetadata:
	def setup_method(self) -> None:
		self.plat = QianchengPlatform(MagicMock())

	def test_name_is_qiancheng(self) -> None:
		assert self.plat.name == "qiancheng"

	def test_display_name_mentions_51job(self) -> None:
		assert "51job" in self.plat.display_name
		assert "前程无忧" in self.plat.display_name

	def test_base_url_points_to_51job(self) -> None:
		assert urlparse(self.plat.base_url).hostname == "www.51job.com"


class TestQianchengNotSupportedEnvelope:
	def setup_method(self) -> None:
		self.client = MagicMock()
		self.plat = QianchengPlatform(self.client)

	def test_candidate_capabilities_return_not_supported(self) -> None:
		for raw in (
			self.plat.search_jobs("Python", city="广州"),
			self.plat.job_detail("job-id"),
			self.plat.recommend_jobs(page=1),
			self.plat.user_info(),
			self.plat.resume_baseinfo(),
			self.plat.resume_expect(),
			self.plat.deliver_list(page=1),
			self.plat.job_card("security-id"),
			self.plat.interview_data(),
			self.plat.chat_history("gid", "security-id"),
			self.plat.friend_label("friend-id", 1),
			self.plat.exchange_contact("security-id", "uid", "friend"),
			self.plat.job_history(page=1),
			self.plat.greet("security-id", "job-id", "你好"),
			self.plat.apply("security-id", "job-id"),
			self.plat.friend_list(page=1),
		):
			assert raw["code"] == -1
			assert raw["error"]["code"] == "NOT_SUPPORTED"
			assert raw["error"]["recoverable"] is True
			assert raw["error"]["details"]["platform"] == "qiancheng"
			assert self.plat.is_success(raw) is False
			code, message = self.plat.parse_error(raw)
			assert code == "NOT_SUPPORTED"
			assert "research backlog" in message
			assert self.plat.unwrap_data(raw) is None

	def test_stub_does_not_delegate_to_client(self) -> None:
		self.plat.search_jobs("Python")
		self.plat.job_detail("job-id")
		self.plat.recommend_jobs()
		self.plat.user_info()
		self.plat.resume_baseinfo()
		self.plat.resume_expect()
		self.plat.deliver_list()
		self.plat.job_card("security-id")
		self.plat.interview_data()
		self.plat.chat_history("gid", "security-id")
		self.plat.friend_label("friend-id", 1)
		self.plat.exchange_contact("security-id", "uid", "friend")
		self.plat.job_history()
		self.plat.greet("security-id", "job-id")
		self.plat.apply("security-id", "job-id")
		self.plat.friend_list()
		self.client.assert_not_called()


class TestQianchengCliVisibility:
	def test_schema_lists_qiancheng_candidate_platform(self, tmp_path) -> None:
		runner = CliRunner()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		choices = payload["data"]["global_options"]["--platform"]["choices"]
		assert "qiancheng" in choices
		assert "51job" in choices

	def test_platform_argument_accepts_qiancheng(self, tmp_path) -> None:
		runner = CliRunner()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--platform", "qiancheng", "schema"])
		assert result.exit_code == 0

	def test_detail_returns_qiancheng_not_supported_envelope(self, tmp_path) -> None:
		runner = CliRunner()
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--platform", "qiancheng", "detail", "fake-id"])
		assert result.exit_code == 1
		payload = json.loads(result.output)
		assert payload["error"]["code"] == "NOT_SUPPORTED"
		assert "51job/前程无忧适配器" in payload["error"]["message"]
		assert "job_card" in payload["error"]["message"]
