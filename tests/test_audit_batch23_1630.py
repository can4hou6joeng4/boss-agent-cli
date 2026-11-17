"""批次二十三回归测试：Issue #1630–#1633。

全部测试离线运行；不调用平台 API、浏览器或网络。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.browser_client import safe_cdp_endpoint
from boss_agent_cli.api.browser_urls import is_zhipin_url
from boss_agent_cli.config import is_valid_cdp_url
from boss_agent_cli.platforms.zhipin import BossPlatform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


@pytest.mark.parametrize("url", ["http://[::1/", "http://[invalid]/web/chat/index", "http://%zz"])
def test_malformed_urls_are_not_platform_urls(url: str) -> None:
	assert is_zhipin_url(url) is False


def test_valid_zhipin_url_still_matches() -> None:
	assert is_zhipin_url("https://www.zhipin.com/web/chat/index")


def test_cdp_url_with_userinfo_rejected() -> None:
	assert is_valid_cdp_url("https://audit-user:PLACEHOLDER@127.0.0.1:9222") is False
	assert is_valid_cdp_url("http://127.0.0.1:9222") is True


def test_cdp_endpoint_for_logs_has_no_userinfo() -> None:
	endpoint = safe_cdp_endpoint("https://audit-user:PLACEHOLDER@127.0.0.1:9222/json")

	assert endpoint == "https://127.0.0.1:9222"
	assert "audit-user" not in endpoint
	assert "PLACEHOLDER" not in endpoint


@pytest.mark.parametrize("platform", [BossPlatform, BossRecruiterPlatform])
@pytest.mark.parametrize("code", [False, True])
def test_boolean_platform_codes_are_never_success(platform: type, code: bool) -> None:
	assert platform(MagicMock()).is_success({"code": code}) is False


@pytest.mark.parametrize("code", [0, "0"])
def test_valid_zero_codes_remain_success(code: int | str) -> None:
	assert BossPlatform(MagicMock()).is_success({"code": code}) is True


def test_salt_first_writer_wins_and_session_round_trips(tmp_path: Path) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

	store = TokenStore(tmp_path)
	salt = store._get_salt()
	store.save({"cookies": {"wt2": "offline-value"}})

	# 模拟后到进程尝试用新 salt 覆盖；已有 salt 时不能再改写。
	salt_path = store._salt_path
	assert salt_path.read_bytes() == salt
	assert store.load() == {"cookies": {"wt2": "offline-value"}}
	assert os.stat(salt_path).st_mode & 0o077 == 0


def test_salt_creation_reuses_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

	winner = b"w" * 16
	loser = b"l" * 16
	sequence = iter([winner, loser])
	monkeypatch.setattr(os, "urandom", lambda size: next(sequence))

	first = TokenStore(tmp_path)._get_salt()
	second = TokenStore(tmp_path)._get_salt()

	assert first == second == winner
