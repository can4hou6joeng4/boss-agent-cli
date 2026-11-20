"""批次七十四回归测试：非 UTF-8 stdout 信封 / 报表与 digest 非对象条目（Issue #2213–#2214）。

全部离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import io
import json

import pytest
from click.testing import CliRunner

from boss_agent_cli import output as output_module
from boss_agent_cli.digest import render_digest_markdown
from boss_agent_cli.main import cli
from boss_agent_cli.output import envelope_error, envelope_success, write_document_stdout


# ── Issue #2213：stdout 不是 UTF-8 时信封与报表仍能写出 ──────────────────


def test_envelope_success_falls_back_to_ascii_for_gbk_stdout(monkeypatch):
	"""cp936 重定向下 emoji 不能让整条命令丢掉信封（Issue #2213）。"""
	monkeypatch.setattr(output_module, "_stdout_encoding", lambda: "gbk")

	text = envelope_success("stats", {"title": "📊 报表", "value": 1})

	assert json.loads(text)["data"]["title"] == "📊 报表"
	assert text.encode("gbk"), "兜底输出必须能按 stdout 编码写出"
	assert "\U0001f4ca" not in text, "无法编码的字符应以 \\uXXXX 转义出现"


def test_envelope_error_falls_back_to_ascii_for_gbk_stdout(monkeypatch):
	monkeypatch.setattr(output_module, "_stdout_encoding", lambda: "gbk")

	text = envelope_error("stats", code="INTERNAL_ERROR", message="注意：📊", recoverable=False, recovery_action="重试")

	payload = json.loads(text)
	assert payload["error"]["message"] == "注意：📊"
	assert text.encode("gbk")


def test_envelope_success_keeps_non_ascii_on_utf8_stdout(monkeypatch):
	"""UTF-8 stdout 的信封保持原样，不为兜底改写成 ASCII（Issue #2213）。"""
	monkeypatch.setattr(output_module, "_stdout_encoding", lambda: "utf-8")

	text = envelope_success("stats", {"title": "📊 报表"})

	assert "📊" in text


class _FakeTty:
	encoding = "gbk"

	def __init__(self) -> None:
		self.written: list[str] = []

	def isatty(self) -> bool:
		return True

	def write(self, text: str) -> None:
		self.written.append(text)

	def flush(self) -> None:
		pass


def test_document_stdout_on_gbk_tty_does_not_crash(monkeypatch):
	fake = _FakeTty()
	monkeypatch.setattr(output_module.sys, "stdout", fake)

	write_document_stdout("<h1>📊 报表</h1>")

	assert "".join(fake.written).encode("gbk"), "终端分支必须输出可编码文本"
	assert "报表" in "".join(fake.written)


class _FakeRedirect:
	def __init__(self) -> None:
		self.buffer = io.BytesIO()

	def isatty(self) -> bool:
		return False

	def write(self, text: str) -> None:
		raise AssertionError("重定向分支应写 UTF-8 字节而不是按 locale 编码")


def test_document_stdout_redirect_writes_utf8_bytes(monkeypatch):
	fake = _FakeRedirect()
	monkeypatch.setattr(output_module.sys, "stdout", fake)

	write_document_stdout("<meta charset=\"UTF-8\">📊")

	assert fake.buffer.getvalue().decode("utf-8") == '<meta charset="UTF-8">📊'


def test_stats_html_output_has_no_emoji_and_renders(tmp_path):
	"""重定向下 emoji 是编码失败源；HTML 报表本身不再带 emoji（Issue #2213）。"""
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path / "data"), "stats", "--format", "html"])

	# 空库也可渲染（0 指标），关键是内容而不是退出码。
	text = result.output
	assert "投递转化漏斗" in text
	assert "📊" not in text and "⚠" not in text


# ── Issue #2214：digest 渲染跳过非对象条目 ──────────────────────────────


@pytest.mark.parametrize(
	"data",
	[
		{"new_matches": ["x", None, 1]},
		{"follow_ups": [None, "y"]},
		{"interviews": [1, None, {"company": "示例公司", "title": "后端", "time": "明天"}]},
	],
)
def test_digest_render_skips_non_object_entries(data):
	text = render_digest_markdown(data)

	assert "每日求职摘要" in text
	assert "AttributeError" not in text


def test_digest_render_counts_only_object_entries():
	text = render_digest_markdown(
		{"new_matches": ["x", {"company": "示例公司", "title": "后端", "salary": "20-30K"}]}
	)

	assert "| 新匹配（待回复） | 1 |" in text
	assert "示例公司" in text
