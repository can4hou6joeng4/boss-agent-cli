"""声明 Accept-Encoding: br, zstd 就必须真的能解码。

真机发的是 ``gzip, deflate, br, zstd``。只在头里照抄而解码器缺失，会在服务端
真按 br/zstd 回包时炸掉——所以这里用 MockTransport 造出真实压缩响应，确认
解码链路成立，而不是只断言字符串。
"""

import brotli
import httpx
import pytest
import zstandard

from boss_agent_cli.api.endpoints import DEFAULT_HEADERS as CANDIDATE_HEADERS
from boss_agent_cli.api.recruiter_endpoints import DEFAULT_HEADERS as RECRUITER_HEADERS

_PAYLOAD = b'{"code":0,"message":"Success","zpData":{"ok":true}}'


@pytest.mark.parametrize("name", ["boss.yaml", "recruiter.yaml"])
def test_default_headers_declare_the_observed_encoding_set(name: str) -> None:
	headers = CANDIDATE_HEADERS if name == "boss.yaml" else RECRUITER_HEADERS

	assert headers["Accept-Encoding"] == "gzip, deflate, br, zstd"


def _client(encoding: str, body: bytes) -> httpx.Client:
	def handler(request: httpx.Request) -> httpx.Response:
		return httpx.Response(200, headers={"Content-Encoding": encoding}, content=body)

	return httpx.Client(transport=httpx.MockTransport(handler), http2=False)


def test_brotli_response_is_decoded() -> None:
	with _client("br", brotli.compress(_PAYLOAD)) as client:
		response = client.get("https://example.test/x")

	assert response.content == _PAYLOAD
	assert response.json()["zpData"]["ok"] is True


def test_zstd_response_is_decoded() -> None:
	compressed = zstandard.ZstdCompressor().compress(_PAYLOAD)
	with _client("zstd", compressed) as client:
		response = client.get("https://example.test/x")

	assert response.content == _PAYLOAD
	assert response.json()["code"] == 0


def test_httpx_advertises_the_same_set_on_its_own() -> None:
	"""装了解码器后 httpx 自己也会声明这四项——依赖与声明因此不会各说各话。"""
	with httpx.Client() as client:
		header = client.headers["Accept-Encoding"]

	for token in ("gzip", "deflate", "br", "zstd"):
		assert token in header
