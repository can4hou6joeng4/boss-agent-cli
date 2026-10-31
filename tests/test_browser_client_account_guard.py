"""探测到的浏览器若属于另一个账号，不得复用它的会话。"""

from unittest.mock import MagicMock

from boss_agent_cli.api.browser_client import BrowserSession


def _session(*, cookies: dict[str, str], context_cookies: list[dict[str, str]]) -> BrowserSession:
	session = BrowserSession(cookies=cookies, user_agent="UA", delay=(0, 0))
	session._context = MagicMock()  # noqa: SLF001
	session._context.cookies.return_value = context_cookies  # noqa: SLF001
	return session


def test_matching_account_is_reused() -> None:
	session = _session(
		cookies={"wt2": "candidate"},
		context_cookies=[{"name": "wt2", "value": "candidate", "domain": ".zhipin.com"}],
	)

	assert session._reused_context_matches_stored_account() is True  # noqa: SLF001


def test_different_account_is_refused() -> None:
	session = _session(
		cookies={"wt2": "candidate"},
		context_cookies=[{"name": "wt2", "value": "recruiter", "domain": ".zhipin.com"}],
	)

	assert session._reused_context_matches_stored_account() is False  # noqa: SLF001


def test_context_without_that_cookie_keeps_existing_reuse_behaviour() -> None:
	"""探测到的浏览器没登录时不构成账号冲突，保持原样复用。"""
	session = _session(cookies={"wt2": "candidate"}, context_cookies=[])

	assert session._reused_context_matches_stored_account() is True  # noqa: SLF001


def test_no_stored_credentials_skips_the_check() -> None:
	"""existing-browser 不读本地凭据，没有可比对象。"""
	session = _session(
		cookies={},
		context_cookies=[{"name": "wt2", "value": "someone", "domain": ".zhipin.com"}],
	)

	assert session._reused_context_matches_stored_account() is True  # noqa: SLF001


def test_cookie_domain_must_match_zhipin() -> None:
	session = _session(
		cookies={"wt2": "candidate"},
		context_cookies=[{"name": "wt2", "value": "candidate", "domain": ".example.com"}],
	)

	# 别的域的 wt2 不是同一个登录态，视为读不到
	assert session._context_cookie_value("wt2") == ""  # noqa: SLF001


def test_cookie_read_failure_does_not_block_reuse() -> None:
	session = _session(cookies={"wt2": "candidate"}, context_cookies=[])
	session._context.cookies.side_effect = RuntimeError("CDP target closed")  # noqa: SLF001

	assert session._context_cookie_value("wt2") == ""  # noqa: SLF001
	assert session._reused_context_matches_stored_account() is True  # noqa: SLF001


def test_try_connect_refuses_context_of_another_account() -> None:
	"""端到端：连到别账号的浏览器时 _try_connect 必须返回 False 且关闭连接。"""
	from unittest.mock import patch

	session = BrowserSession(cookies={"wt2": "candidate"}, user_agent="UA", delay=(0, 0))
	context = MagicMock()
	context.cookies.return_value = [{"name": "wt2", "value": "recruiter", "domain": ".zhipin.com"}]
	browser = MagicMock()
	browser.contexts = [context]
	session._pw = MagicMock()  # noqa: SLF001
	session._pw.chromium.connect_over_cdp.return_value = browser  # noqa: SLF001

	with patch.object(session, "_close_browser_quietly") as closed:
		assert session._try_connect("http://127.0.0.1:9222") is False  # noqa: SLF001

	closed.assert_called_once()
