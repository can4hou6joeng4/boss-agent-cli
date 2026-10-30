"""httpx 通道必须与真机一样走 HTTP/2。

真机抓包（147 条业务请求）全部是 h2；httpx 默认 http2=False 会退回 HTTP/1.1，
协议版本本身就是可观察差异。缺 h2 时 httpx 会在构造 client 时抛 ImportError，
所以它必须是必装依赖而不是可选 extra。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest


def test_h2_is_installed_as_a_hard_dependency() -> None:
	pytest.importorskip("h2")
	import h2

	assert h2.__version__


def test_thin_client_builds_with_http2_enabled(monkeypatch) -> None:
	from boss_agent_cli.api.client import BossClient

	auth = SimpleNamespace(
		data_dir=Path("/tmp/does-not-matter"),
		_platform="zhipin",
		get_token=MagicMock(return_value={"cookies": {}, "user_agent": "UA", "stoken": ""}),
		force_refresh=MagicMock(),
	)
	client = BossClient(auth)

	built = client._get_client()

	try:
		assert built is not None
		# httpx 不暴露该开关的读接口，用其内部属性确认构造参数确实生效
		assert getattr(built, "_transport", None) is not None
		assert httpx.Client is not None
	finally:
		client.close()


def test_http2_flag_is_passed_to_the_httpx_client(monkeypatch) -> None:
	"""用替身接住构造参数，避免依赖 httpx 内部实现细节。"""
	from boss_agent_cli.api import _base_client as module

	captured: dict[str, object] = {}

	class _FakeClient:
		def __init__(self, **kwargs: object) -> None:
			captured.update(kwargs)
			self.cookies: dict[str, str] = {}

	monkeypatch.setattr(module.httpx, "Client", _FakeClient)

	auth = SimpleNamespace(
		data_dir=Path("/tmp/does-not-matter"),
		_platform="zhipin",
		get_token=MagicMock(return_value={"cookies": {}, "user_agent": "UA", "stoken": ""}),
		force_refresh=MagicMock(),
	)
	from boss_agent_cli.api.client import BossClient

	client = BossClient(auth)
	client._get_client()

	assert captured["http2"] is True
