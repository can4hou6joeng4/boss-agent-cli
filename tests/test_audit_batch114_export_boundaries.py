"""批次 114：导出边界（Issue #2457 / #2458 / #2459）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from boss_agent_cli.commands._output_path import _PROBLEM_CONTRACTS, validate_output_path_or_emit
from boss_agent_cli.services import output_path as output_path_module
from boss_agent_cli.services.chat_utils import sanitize_csv_cell
from boss_agent_cli.services.json_files import strict_json_text
from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path
from boss_agent_cli.services.text_files import write_text_atomic
from boss_agent_cli.wizard.actions import _EXPORT_OUTPUT_CONTRACTS


def _assert_no_raw_surrogates(text: str) -> None:
	assert all(not 0xD800 <= ord(char) <= 0xDFFF for char in text)
	text.encode("utf-8")


# ── Issue #2457：孤立代理项必须写成字面文本，不能是裸 JSON 转义 ─────


def test_strict_json_text_lone_surrogates_become_literal_text() -> None:
	text = strict_json_text({"name": "bad\ud800name", "v\udcff": "x"})

	_assert_no_raw_surrogates(text)
	parsed = json.loads(text)
	assert parsed == {"name": "bad\\ud800name", "v\\udcff": "x"}
	# 解析回的值同样不含裸代理项：严格解析器（jq）不会再遇到未配对转义。
	_assert_no_raw_surrogates(json.dumps(parsed))


def test_strict_json_text_lone_surrogate_survives_atomic_write(tmp_path: Path) -> None:
	text = strict_json_text({"name": "bad\ud800name"})
	path = write_text_atomic(tmp_path / "out.json", text)
	raw = path.read_bytes().decode("utf-8")

	_assert_no_raw_surrogates(raw)
	assert json.loads(raw) == {"name": "bad\\ud800name"}


def test_strict_json_text_merges_valid_surrogate_pairs() -> None:
	# json.loads 会把转义对合并成一个码点；但 utf-16 surrogatepass 解码等来源
	# 会保留两个码元，序列化前必须合并，不能当成两个孤立代理项。
	pair = "".join(map(chr, (0xD83D, 0xDE00)))
	assert len(pair) == 2

	text = strict_json_text({"emoji": pair, pair: "key"})

	_assert_no_raw_surrogates(text)
	assert json.loads(text) == {"emoji": "😀", "😀": "key"}


def test_strict_json_text_keeps_ordinary_astral_text() -> None:
	assert json.loads(strict_json_text({"emoji": "😀"})) == {"emoji": "😀"}


# ── Issue #2458：APFS 无法创建的文件名在预检期拒绝 ─────────────────


def _set_requires_utf8(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
	monkeypatch.setattr(output_path_module, "_FS_REQUIRES_UTF8_NAMES", enabled)


def test_inspect_rejects_non_utf8_name_on_apfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_set_requires_utf8(monkeypatch, True)

	target, problem = inspect_output_path(tmp_path / "bad\udc80name.json")

	assert problem is OutputPathProblem.FILENAME_NOT_UTF8
	assert str(target).endswith("bad\udc80name.json")


def test_inspect_rejects_lone_high_surrogate_on_apfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_set_requires_utf8(monkeypatch, True)

	_, problem = inspect_output_path(tmp_path / "bad\ud800name.json")

	assert problem is OutputPathProblem.FILENAME_NOT_UTF8


def test_inspect_accepts_non_ascii_utf8_names_on_apfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_set_requires_utf8(monkeypatch, True)

	_, problem = inspect_output_path(tmp_path / "中文-导出.json")

	assert problem is None


def test_inspect_skips_utf8_check_when_fs_allows_arbitrary_bytes(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	_set_requires_utf8(monkeypatch, False)

	_, problem = inspect_output_path(tmp_path / "bad\udc80name.json")

	assert problem is None


def test_output_path_contracts_cover_every_problem() -> None:
	assert set(_PROBLEM_CONTRACTS) == set(OutputPathProblem)
	assert set(_EXPORT_OUTPUT_CONTRACTS) == set(OutputPathProblem)


def test_validate_emits_invalid_param_for_non_utf8_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_set_requires_utf8(monkeypatch, True)
	payloads: list[dict[str, object]] = []

	class _Ctx:
		def exit(self, code: int = 0) -> None:
			raise SystemExit(code)

	with patch(
		"boss_agent_cli.display.handle_error_output",
		side_effect=lambda ctx, *args, **kwargs: payloads.append(kwargs),
	):
		result = validate_output_path_or_emit(_Ctx(), "export", tmp_path / "bad\udc80name.json")

	assert result is None
	assert payloads[0]["code"] == "INVALID_PARAM"
	assert "UTF-8" in str(payloads[0]["message"])


# ── Issue #2459：前导控制字符不参与公式前缀判定 ────────────────────

_LEADING_CONTROLS = ("\n", "\x0b", "\x0c", "\x00", "﻿")


@pytest.mark.parametrize("lead", _LEADING_CONTROLS)
def test_csv_leading_control_char_before_formula_gets_prefix(lead: str) -> None:
	assert sanitize_csv_cell(f"{lead}=cmd|'!A0") == f"'{lead}=cmd|'!A0"


@pytest.mark.parametrize("lead", _LEADING_CONTROLS)
def test_csv_leading_control_char_before_placeholder_dash_unchanged(lead: str) -> None:
	assert sanitize_csv_cell(f"{lead}-") == f"{lead}-"


@pytest.mark.parametrize("lead", _LEADING_CONTROLS)
def test_csv_leading_control_char_before_plain_text_unchanged(lead: str) -> None:
	assert sanitize_csv_cell(f"{lead}普通文本") == f"{lead}普通文本"


def test_csv_formula_after_leading_space_still_not_a_formula() -> None:
	# 空格不是表格软件忽略的前导控制字符，保持既有语义。
	assert sanitize_csv_cell(" =cmd") == " =cmd"
	assert sanitize_csv_cell("普通\n文本") == "普通\n文本"
