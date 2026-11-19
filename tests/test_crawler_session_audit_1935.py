"""crawler 会话、Hook 清单与整数配置的边界（Issue #1935–#1938）。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import types

import pytest

from boss_agent_cli.crawler.hooks import (
	HOOK_SCRIPT_NAMES,
	HookRegistrationError,
	inject_hook_profile,
	verify_hook_profile,
)
from boss_agent_cli.crawler.service import CrawlSettings, crawl_int_setting
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.cache.store import CacheRecordInvalid


def _write_hooks(root: Path) -> Path:
	root.mkdir(parents=True, exist_ok=True)
	lines = []
	for index, (_, filename) in enumerate(HOOK_SCRIPT_NAMES, 1):
		payload = f"globalThis.hook{index} = true;\n".encode()
		(root / filename).write_bytes(payload)
		lines.append(f"{filename} {sha256(payload).hexdigest()}")
	(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
	return root


# ── Issue #1935：Hook 必须先于 seed 导航注册 ─────────────────────────


def test_hooks_register_before_seed_navigation(monkeypatch, tmp_path) -> None:
	events: list[str] = []

	class Options:
		def set_local_port(self, port: int) -> None:
			pass

		def set_user_data_path(self, path: str) -> None:
			pass

	class Listener:
		def start(self, *args, **kwargs) -> None:
			events.append("listen")

	class Page:
		url = "https://www.zhipin.com/web/geek/jobs"
		html = "<div class='job-list-box'></div>"

		def __init__(self) -> None:
			self.listen = Listener()

		def run_cdp(self, command: str, **kwargs) -> None:
			events.append("hook")

		def get(self, url: str) -> None:
			events.append("navigate")

		class set:
			@staticmethod
			def cookies(items) -> None:
				events.append("cookies")

	page = Page()
	monkeypatch.setitem(
		sys.modules,
		"DrissionPage",
		types.SimpleNamespace(ChromiumOptions=Options, ChromiumPage=lambda options: page),
	)
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="screenshot-full",
		hook_dir=_write_hooks(tmp_path / "hooks"),
		seed_cookies={"wt2": "t"},
	)

	session.open()

	assert events.index("navigate") > max(
		index for index, event in enumerate(events) if event == "hook"
	)


# ── Issue #1936：超长整数等裸 ValueError 不得逃逸 ───────────────────


def _session(tmp_path: Path) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
		hook_dir=None,
	)


def test_normalize_response_body_keeps_text_for_huge_integer(tmp_path) -> None:
	body = '{"n": ' + "1" * 5000 + "}"

	assert DrissionCrawlerSession._normalize_response_body(body) == body


def test_decode_fetch_response_survives_huge_integer_payload(tmp_path) -> None:
	session = _session(tmp_path)
	huge = '{"n": ' + "1" * 5000 + "}"
	raw = '{"status": 200, "text": ' + _json_string(huge) + ', "url": "https://www.zhipin.com/wapi/test"}'

	# 裸 ValueError 不得逃逸成未知异常；按既有「响应不是 JSON」契约报 RuntimeError。
	with pytest.raises(RuntimeError, match="不是 JSON"):
		session._decode_fetch_response(raw)


def test_decode_fetch_response_reports_unparseable_transport(tmp_path) -> None:
	session = _session(tmp_path)

	with pytest.raises(RuntimeError):
		session._decode_fetch_response('{"n": ' + "1" * 5000 + "}")


def _json_string(value: str) -> str:
	import json

	return json.dumps(value)


# ── Issue #1937：非 ASCII 清单走 HookRegistrationError 契约 ──────────


def test_non_ascii_checksums_report_hook_registration_error(tmp_path) -> None:
	root = _write_hooks(tmp_path / "hooks")
	manifest = root / "SHA256SUMS"
	manifest.write_text("备注\n" + manifest.read_text(encoding="ascii"), encoding="utf-8")

	with pytest.raises(HookRegistrationError) as caught:
		inject_hook_profile(object(), "screenshot-full", root)

	assert "ASCII" in str(caught.value)

	with pytest.raises(ValueError) as verify_caught:
		verify_hook_profile("screenshot-full", root)

	assert "ASCII" in str(verify_caught.value)


# ── Issue #1938：整数配置拒绝 bool 与 float ─────────────────────────


@pytest.mark.parametrize("value", [True, False, 2.9, 1e400])
def test_crawl_int_setting_rejects_bool_and_float(value) -> None:
	with pytest.raises(ValueError):
		crawl_int_setting({"k": value}, "k", 20)


def test_crawl_int_setting_accepts_int_and_digit_string() -> None:
	assert crawl_int_setting({"k": 5}, "k", 20) == 5
	assert crawl_int_setting({"k": "7"}, "k", 20) == 7


def test_crawl_settings_from_dict_rejects_float_limits(tmp_path) -> None:
	settings = CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)
	raw = settings.as_dict()
	raw["limits"]["max_seconds"] = 2.9

	with pytest.raises(CacheRecordInvalid):
		CrawlSettings.from_dict(raw, run_id="run-1")
