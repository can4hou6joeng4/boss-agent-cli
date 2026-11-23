"""审计批次 134：向导空白文本输入与 CLI 同口径拒绝（Issue #2523）。

CLI `ai` 命令族自 Issue #1975 起统一拒绝空白文本（``_ensure_text_not_blank``），
`exchange` / `mark` / `chatmsg` / `chat-summary` 共用 ``resolve_friend_or_emit``
对空白联系人标识直接报 ``INVALID_PARAM``。向导侧 `ai_assist` 的 ``resume`` /
``prompt`` 与三个联系人动作仍只查类型：空白提示词会照常发起一次计费 AI
请求，空白标识会先翻一遍沟通列表再报 ``CONTACT_NOT_FOUND``。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.output import Logger
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


class _FakeCandidatePlatform:
	def __init__(self) -> None:
		self.calls: list[tuple[object, ...]] = []

	def __enter__(self) -> "_FakeCandidatePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, *, page: int = 1) -> dict[str, object]:
		self.calls.append(("friend_list", page))
		return {
			"code": 0,
			"data": {
				"result": [{"uid": 10001, "securityId": "sid_current", "name": "联系人甲", "friendSource": 0}],
				"hasMore": False,
			},
		}

	def exchange_contact(self, security_id: str, uid: str, name: str, exchange_type: int = 1) -> dict[str, object]:
		self.calls.append(("exchange_contact", security_id, uid, name, exchange_type))
		return {"code": 0, "data": {"requested": True}}

	def friend_label(self, uid: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict[str, object]:
		self.calls.append(("friend_label", uid, label_id, friend_source, remove))
		return {"code": 0, "data": {"updated": True}}

	def chat_history(self, gid: str, security_id: str, *, page: int = 1, count: int = 20) -> dict[str, object]:
		self.calls.append(("chat_history", gid, security_id, page, count))
		return {"code": 0, "data": {"messages": [{"text": "你好"}]}}

	def is_success(self, response: dict[str, object]) -> bool:
		return response["code"] == 0

	def unwrap_data(self, response: dict[str, object]) -> object:
		return response["data"]


def _forbidden_factory(*args: object, **kwargs: object) -> object:
	raise AssertionError("非法输入不应构造平台实例")


def _context(tmp_path: Path, *, candidate: object | None = None) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=(lambda *args, **kwargs: candidate) if candidate is not None else _forbidden_factory,
		recruiter_factory=_forbidden_factory,
	)


def _configure_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="moonshot", ai_model="moonshot-v1-8k")
	store.save_api_key("sk-test")


@pytest.mark.parametrize("action,key", [("candidate_exchange", "security_id"), ("candidate_mark", "security_id")])
def test_contact_actions_reject_blank_security_id(tmp_path: Path, action: str, key: str) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS[action](_context(tmp_path), {key: "   ", "type": "phone", "label": "沟通中"}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert key in str(exc_info.value)


def test_chat_history_rejects_blank_gid(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_chat_history"](_context(tmp_path), {"gid": "  "}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "gid" in str(exc_info.value)


def test_chat_history_preserves_padded_gid(tmp_path: Path) -> None:
	"""CLI 只判 ``.strip()``、不改写参数；向导不能用 strip 后的值寻址。"""
	platform = _FakeCandidatePlatform()

	result = DEFAULT_ACTIONS["candidate_chat_history"](_context(tmp_path, candidate=platform), {"gid": " 10001 "}, {})

	assert result.data["uid"] == "10001"
	assert platform.calls[0] == ("friend_list", 1)


def test_ai_assist_rejects_blank_prompt_before_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_configure_ai(tmp_path, monkeypatch)
	post = MagicMock(side_effect=AssertionError("空白 prompt 不应发起 AI 请求"))

	with patch("boss_agent_cli.ai.service.httpx.post", post):
		with pytest.raises(WorkflowActionError) as exc_info:
			DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "default", "prompt": "   "}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert "prompt" in str(exc_info.value)
	post.assert_not_called()


def test_ai_assist_rejects_blank_resume_before_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_configure_ai(tmp_path, monkeypatch)
	post = MagicMock(side_effect=AssertionError("空白简历名不应发起 AI 请求"))

	with patch("boss_agent_cli.ai.service.httpx.post", post):
		with pytest.raises(WorkflowActionError) as exc_info:
			DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "  ", "prompt": "优化"}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert "resume" in str(exc_info.value)
	assert exc_info.value.code != "RESUME_NOT_FOUND"
	post.assert_not_called()


def test_ai_assist_preserves_padded_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""与 CLI 只判空白、原样透传一致：首尾空格不能被向导悄悄改写。"""
	_configure_ai(tmp_path, monkeypatch)
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = {"choices": [{"message": {"content": "建议"}}]}

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=response) as post:
		result = DEFAULT_ACTIONS["ai_assist"](_context(tmp_path), {"resume": "default", "prompt": " 优化 "}, {})

	assert result.data["result"] == "建议"
	user_message = post.call_args.kwargs["json"]["messages"][1]["content"]
	assert user_message.startswith(" 优化 \n\n简历:")
