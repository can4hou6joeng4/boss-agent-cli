"""httpx-only 失败时，信封里必须给出「回退默认通道」的可执行指引。"""

from types import SimpleNamespace

from boss_agent_cli.display import httpx_only_fallback_hint

#: 该文件此前被 .gitignore 排除过一版；这里用的是独立新文件，确保能入库。


def _ctx(browser_source: str | None) -> SimpleNamespace:
	return SimpleNamespace(obj={} if browser_source is None else {"browser_source": browser_source})


def test_hint_is_absent_for_the_default_source() -> None:
	assert httpx_only_fallback_hint(_ctx("auto")) == []
	assert httpx_only_fallback_hint(_ctx(None)) == []


def test_hint_tells_the_operator_to_fall_back_to_auto() -> None:
	hints = httpx_only_fallback_hint(_ctx("httpx-only"))

	assert hints
	assert any("--browser-source auto" in hint for hint in hints)
	assert any("checkpoint" in hint for hint in hints)


def test_hint_is_absent_for_other_explicit_sources() -> None:
	for source in ("stored-cookie", "existing-browser"):
		assert httpx_only_fallback_hint(_ctx(source)) == []
