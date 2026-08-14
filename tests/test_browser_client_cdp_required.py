"""browser_mode="cdp_required" 严格模式的测试。

cdp_required 是公开安全开关：跳过 Bridge、CDP 失败立即抛错、永不降级 headless。
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.api.browser_client import BrowserSession


def test_cdp_required_skips_bridge_and_headless_when_cdp_ok():
	"""cdp_required 时：跳过 Bridge，CDP 成功则直接返回，永不 headless。"""
	session = BrowserSession(cookies={}, user_agent="", browser_mode="cdp_required")
	with (
		patch.object(session, "_try_cdp", return_value=True) as mock_try_cdp,
		patch("boss_agent_cli.api.browser_client.sync_playwright") as mock_sync_playwright,
		patch.object(session, "_try_bridge") as mock_try_bridge,
		patch.object(session, "_start_headless") as mock_start_headless,
	):
		mock_sync_playwright.return_value.start.return_value = MagicMock()
		session._ensure_started()

	mock_try_bridge.assert_not_called()
	mock_start_headless.assert_not_called()
	mock_try_cdp.assert_called_once()


def test_cdp_required_raises_when_cdp_unavailable():
	"""cdp_required 且 CDP 失败时：抛异常，绝不降级 headless。"""
	session = BrowserSession(cookies={}, user_agent="", browser_mode="cdp_required")
	with (
		patch.object(session, "_try_cdp", return_value=False) as mock_try_cdp,
		patch("boss_agent_cli.api.browser_client.sync_playwright") as mock_sync_playwright,
		patch.object(session, "_start_headless") as mock_start_headless,
	):
		mock_sync_playwright.return_value.start.return_value = MagicMock()
		with pytest.raises(RuntimeError, match="CDP"):
			session._ensure_started()

	mock_start_headless.assert_not_called()
	mock_try_cdp.assert_called_once()
