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

	def test_required_capabilities_return_not_supported(self) -> None:
		for raw in (
			self.plat.search_jobs("Python", city="广州"),
			self.plat.job_detail("job-id"),
			self.plat.recommend_jobs(page=1),
			self.plat.user_info(),
		):
			assert raw["code"] == "NOT_SUPPORTED"
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
