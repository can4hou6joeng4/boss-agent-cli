"""AI 配置、参数与错误脱敏的边界（Issue #1950–#1953）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.main import cli

_KEY = "FAKEKEY-not-real-0001"


def _invoke(data_dir: Path, *args: str) -> tuple[int, dict]:
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, json.loads(result.stdout)


# ── Issue #1950：坏 salt 必须有信封 ─────────────────────────────────


def test_salt_directory_reports_local_io_envelope(tmp_path: Path) -> None:
	(tmp_path / "auth" / "salt").mkdir(parents=True)

	code, envelope = _invoke(tmp_path, "ai", "config", "--api-key", _KEY)

	assert code == 1
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "LOCAL_IO_ERROR"
	assert "salt" in envelope["error"]["message"]


# ── Issue #1951：显式空 --resume 不得发起请求 ───────────────────────


@pytest.mark.parametrize(
	"args",
	[
		("ai", "reply", "hi", "--resume", ""),
		("ai", "interview-prep", "jd", "--resume", ""),
		("ai", "chat-coach", "chat", "--resume", ""),
	],
)
def test_empty_resume_is_invalid_param(tmp_path: Path, monkeypatch, args) -> None:
	def no_request(*a: object, **k: object) -> None:
		raise AssertionError("不应发起 AI 请求")

	monkeypatch.setattr(httpx, "post", no_request)
	_invoke(tmp_path, "ai", "config", "--provider", "openai", "--model", "gpt-4o", "--api-key", _KEY)

	code, envelope = _invoke(tmp_path, *args)

	assert code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "简历名称" in envelope["error"]["message"]


# ── Issue #1952：密钥必须能放进请求头 ───────────────────────────────


@pytest.mark.parametrize("key", ["FAKEKEY-假-0004", "FAKEKEY-not\nreal-0005", "FAKEKEY-not\x00real"])
def test_unsendable_api_key_is_rejected(tmp_path: Path, key: str) -> None:
	code, envelope = _invoke(tmp_path, "ai", "config", "--api-key", key)

	assert code == 1
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert not (tmp_path / "ai" / "api_key.enc").exists()


def test_api_key_surrounding_whitespace_is_stripped(tmp_path: Path) -> None:
	from boss_agent_cli.ai.config import AIConfigStore

	code, envelope = _invoke(tmp_path, "ai", "config", "--api-key", f"  {_KEY}\r\n")

	assert code == 0, envelope
	stores = [AIConfigStore(tmp_path), AIConfigStore(tmp_path / "ai")]
	assert _KEY in {store.get_api_key() for store in stores}


# ── Issue #1953：回显的密钥不得进入错误信息 ─────────────────────────


def _service(key: str, response: httpx.Response, monkeypatch) -> AIService:
	response.request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
	monkeypatch.setattr(httpx, "post", lambda *a, **k: response)
	return AIService(base_url="https://example.invalid/v1", api_key=key, model="m")


@pytest.mark.parametrize("key", [_KEY, "sk-FAKE0000aaaa.FAKETAIL9999zzzz", "FAKEID000001.FAKESECRET0002"])
def test_echoed_key_is_masked_in_http_error(key: str, monkeypatch) -> None:
	response = httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {key}. Check it."}})
	service = _service(key, response, monkeypatch)

	with pytest.raises(AIServiceError) as caught:
		service.chat([{"role": "user", "content": "hi"}])

	message = str(caught.value)
	for fragment in key.split("."):
		assert fragment not in message
	assert "Incorrect API key" in message


def test_echoed_key_is_masked_in_embedded_error(monkeypatch) -> None:
	response = httpx.Response(200, json={"error": {"message": f"invalid token {_KEY}", "code": 401}})
	service = _service(_KEY, response, monkeypatch)

	with pytest.raises(AIServiceError) as caught:
		service.chat([{"role": "user", "content": "hi"}])

	assert _KEY not in str(caught.value)
