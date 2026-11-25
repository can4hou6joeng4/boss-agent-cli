"""boss ai local status 的 TTY 安全标量（Issue #2820）。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import click
import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands import ai_local


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200, emoji=False))
	return stream


def _render(rendered: io.StringIO, **overrides: Any) -> str:
	ctx = click.Context(click.Command("ai"))
	ctx.obj = {"data_dir": Path("/tmp/demo-data"), "platform": "zhipin"}
	data: dict[str, Any] = {
		"configured": True,
		"provider": "ollama",
		"model": "qwen3:14b",
		"base_url": "http://example.invalid",
		"recommended_models": [],
	}
	data.update(overrides)
	ai_local._render_local_status(ctx, data)
	return rendered.getvalue()


def _assert_no_repr(text: str) -> None:
	assert "{'" not in text
	assert "nan" not in text
	assert "inf" not in text
	assert "None" not in text
	assert "True" not in text
	assert "False" not in text


def test_normal_values_are_kept(rendered: io.StringIO) -> None:
	text = _render(rendered)

	assert "runtime: ollama" in text
	assert "model: qwen3:14b" in text
	assert "base_url: http://example.invalid" in text


@pytest.mark.parametrize(
	"value",
	[10**5000, {"a": 1}, ["a"], True, float("nan"), float("inf")],
	ids=["huge", "dict", "list", "bool", "nan", "inf"],
)
@pytest.mark.parametrize("field", ["provider", "model", "base_url"])
def test_config_field_drift_falls_back(rendered: io.StringIO, field: str, value: Any) -> None:
	text = _render(rendered, **{field: value})

	_assert_no_repr(text)
	# "model: -" 是缺失占位；其它行仍正常渲染。
	assert "model: " in text
	assert "runtime: " in text
	assert "base_url: " in text


def test_recommended_row_drift_falls_back(rendered: io.StringIO) -> None:
	text = _render(
		rendered,
		recommended_models=[
			{
				"name": 10**5000,
				"runtime": {"a": 1},
				"min_memory_gb": float("nan"),
				"description": True,
				"recommended": True,
			}
		],
	)

	_assert_no_repr(text)
	assert "推荐模型" in text
	assert " GB" in text


def test_recommended_row_normal_values_are_kept(rendered: io.StringIO) -> None:
	text = _render(
		rendered,
		recommended_models=[
			{
				"name": "qwen3:14b",
				"runtime": "ollama",
				"min_memory_gb": 16,
				"description": "适合日常使用",
				"recommended": True,
			}
		],
	)

	assert "qwen3:14b" in text
	assert "ollama" in text
	assert "16 GB" in text
	assert "适合日常使用" in text
	assert "★" in text


def test_unconfigured_hides_runtime_lines(rendered: io.StringIO) -> None:
	text = _render(rendered, configured=False)

	assert "未配置" in text
	assert "runtime:" not in text
	assert "base_url:" not in text


def test_markup_payload_stays_literal(rendered: io.StringIO) -> None:
	payload = "[bold]x[/bold]"
	text = _render(rendered, provider=payload, model=payload, base_url=payload)

	assert payload in text
