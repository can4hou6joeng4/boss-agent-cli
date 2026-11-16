"""平台载荷非对象真值的边界收口（Issue #1233）。

``unwrap_data(...) or {}`` 只挡 ``null`` 与空容器；平台把 ``zpData`` 返回成
字符串 / 数组 / 数字 / 布尔时，后续 ``.get`` 抛 ``AttributeError``、``**`` 抛
``TypeError``，被兜成不可重试的 ``INTERNAL_ERROR``，或把畸形值原样放进
``ok:true`` 的 ``data``。这里按各命令期望的容器类型断言兜底结果。
"""

import json
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.models import platform_data_dict
from boss_agent_cli.main import cli

_NON_OBJECTS = ["oops", ["oops"], 7, True]


class _StubPlatform:
	"""成功包络 + 任意 ``zpData`` 的平台替身。"""

	name = "zhipin"

	def __init__(self, payload):
		self.payload = payload

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def is_success(self, response):
		return isinstance(response, dict) and response.get("code") == 0

	def unwrap_data(self, response):
		return response.get("zpData") if isinstance(response, dict) else None

	def parse_error(self, response):
		return "UNKNOWN", ""

	def __getattr__(self, name):
		if name.startswith("_"):
			raise AttributeError(name)
		return lambda *args, **kwargs: {"code": 0, "zpData": self.payload}


def _invoke(module: str, args: list[str], payload) -> dict:
	with tempfile.TemporaryDirectory() as data_dir:
		target = __import__(module, fromlist=["x"])
		with ExitStack() as stack:
			for attr in ("get_platform_instance", "get_recruiter_platform_instance", "AuthManager"):
				if not hasattr(target, attr):
					continue
				value = (
					(lambda *a, **kw: _StubPlatform(payload))
					if attr.startswith("get_")
					else (lambda *a, **kw: MagicMock())
				)
				stack.enter_context(patch.object(target, attr, value))
			result = CliRunner().invoke(cli, ["--data-dir", data_dir, "--json", *args])
	return json.loads(result.output)


# ── helper ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [*_NON_OBJECTS, None, [], " "])
def test_platform_data_dict_normalizes_non_object_payloads(payload):
	assert platform_data_dict(payload) == {}


def test_platform_data_dict_keeps_object_payloads():
	payload = {"friendList": []}
	assert platform_data_dict(payload) is payload


# ── 只读招聘者命令 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
	("module", "args", "key"),
	[
		("boss_agent_cli.commands.recruiter.jobs", ["hr", "jobs", "detail", "job-1"], None),
		("boss_agent_cli.commands.me", ["me", "--section", "resume"], "resume"),
		("boss_agent_cli.commands.me", ["me", "--section", "expect"], "expect"),
		("boss_agent_cli.commands.me", ["me", "--section", "deliver"], "deliver"),
	],
	ids=["jobs-detail", "me-resume", "me-expect", "me-deliver"],
)
def test_structured_readonly_commands_normalize_non_object_payload(module, args, key):
	payload = _invoke(module, args, "oops")

	assert payload["ok"] is True
	raw = json.dumps(payload["data"], ensure_ascii=False)
	if key is None:
		assert "oops" not in raw
	else:
		# ``me`` 的 data 是按 section 命名的容器，平台原值照旧透传（Issue #1233
		# 只收口崩溃点，不改变既有透传契约）。
		assert payload["data"] == {key: "oops"}


@pytest.mark.parametrize(
	("module", "args"),
	[
		("boss_agent_cli.commands.recruiter.applications", ["hr", "applications"]),
		("boss_agent_cli.commands.recruiter.candidates", ["hr", "candidates"]),
		("boss_agent_cli.commands.recruiter.jobs", ["hr", "jobs", "list"]),
		("boss_agent_cli.commands.recruiter.recommendations", ["hr", "recommendations", "--job-id", "job-1"]),
		("boss_agent_cli.commands.recruiter.chat", ["hr", "chatmsg", "1"]),
	],
	ids=["applications", "candidates", "jobs-list", "recommendations", "chatmsg"],
)
def test_passthrough_list_commands_keep_platform_payload(module, args):
	"""列表命令的 ``data`` 有意保留平台原值（见 ``test_recruiter_jobs_list``）。"""

	payload = _invoke(module, args, "oops")

	assert payload["ok"] is True
	assert payload["data"] == "oops"


# ── download-resume ─────────────────────────────────────────────────


@pytest.mark.parametrize("raw_payload", [*_NON_OBJECTS, None])
def test_download_resume_reports_success_for_non_object_payload(raw_payload):
	with tempfile.TemporaryDirectory() as data_dir:
		target = __import__(
			"boss_agent_cli.commands.recruiter.download_resume", fromlist=["x"]
		)
		with (
			patch.object(target, "get_recruiter_platform_instance", lambda *a, **kw: _StubPlatform(raw_payload)),
			patch.object(target, "AuthManager", lambda *a, **kw: MagicMock()),
		):
			result = CliRunner().invoke(
				cli,
				[
					"--data-dir",
					data_dir,
					"--json",
					"hr",
					"download-resume",
					"1",
					"--message-id",
					"2",
					"--output",
					f"{data_dir}/x.pdf",
				],
			)

	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"]["downloaded"] is True


# ── wizard last_messages ────────────────────────────────────────────


@pytest.mark.parametrize("raw_payload", _NON_OBJECTS)
def test_wizard_last_messages_treats_non_object_payload_as_empty_page(raw_payload):
	class _Auth:
		def check_status(self):
			return {"token": "synthetic"}

	platform = _StubPlatform(raw_payload)
	with tempfile.TemporaryDirectory() as data_dir:
		with (
			patch("boss_agent_cli.wizard.actions.ActionContext.auth", lambda self: _Auth()),
			patch("boss_agent_cli.wizard.actions.ActionContext.recruiter_platform", lambda self: platform),
		):
			result = CliRunner().invoke(
				cli,
				[
					"--data-dir",
					data_dir,
					"--json",
					"wizard",
					"--input-json",
					json.dumps({"role": "recruiter", "platform": "zhipin", "goal": "last_messages"}),
				],
			)

	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"]["status"] == "completed"
	assert envelope["data"]["last_result"]["data"]["friend_ids"] == []
	assert envelope["data"]["last_result"]["data"]["messages"] == []


# ── favorites list ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw_payload", _NON_OBJECTS)
def test_favorites_list_reports_retryable_network_error(raw_payload):
	envelope = _invoke("boss_agent_cli.commands.favorites", ["favorites", "list"], raw_payload)

	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "NETWORK_ERROR"
	assert envelope["error"]["recoverable"] is True
	assert "职位收藏响应格式无效" in envelope["error"]["message"]
