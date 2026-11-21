"""批次 89 审计修复：简历导出/导入与本地模型导入边界（Issue #2356–#2361）。"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from boss_agent_cli.ai.config import AIConfigValueError, normalize_base_url
from boss_agent_cli.ai.local_models import LocalModelManifestError, import_local_model
from boss_agent_cli.resume.store import ResumeImportError, ResumeStore


def _deep_resume_document() -> str:
	"""990 层嵌套的 rows.content：json.loads 能读，写回时耗尽递归栈。"""
	depth = 990
	content = "[" * depth + "]" * depth
	return (
		'{"name":"张测试","title":"t","modules":[{"id":"m","title":"M","rows":'
		'[{"type":"richtext","content":' + content + "}]}]}"
	)


# ── Issue #2356：export_json 深嵌套不再裸 RecursionError ─────────────


def test_export_json_deep_nesting_raises_local_state_error(tmp_path: Path) -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "deep.json").write_text(_deep_resume_document(), encoding="utf-8")
	store = ResumeStore(resumes)

	with pytest.raises(OSError) as excinfo:
		store.export_json("deep")

	assert excinfo.value.errno == errno.EINVAL
	assert "无法序列化" in str(excinfo.value)


# ── Issue #2357：孤立代理项 + portable 清洗字符不再打断列表 ──────────


def test_list_handles_name_with_surrogate_and_portable_chars(tmp_path: Path) -> None:
	resumes = tmp_path / "resumes"
	resumes.mkdir()
	(resumes / "evil.json").write_text(json.dumps({"name": "a\ud800*", "title": "t"}), encoding="utf-8")
	store = ResumeStore(resumes)

	items, corrupt = store.list_all_with_corrupt()

	assert [item["name"] for item in items] == ["evil"]
	assert corrupt == []
	# delete / edit 走 stored_name：恢复命令本身必须可用。
	assert store.stored_name("evil") == "evil"


# ── Issue #2358：导入深嵌套文件报输入错误而不是本地 IO ──────────────


def test_import_deep_nesting_reports_input_error(tmp_path: Path) -> None:
	source = tmp_path / "deep.json"
	source.write_text(_deep_resume_document(), encoding="utf-8")
	store = ResumeStore(tmp_path / "resumes")

	with pytest.raises(ResumeImportError, match="嵌套层级过深"):
		store.import_file(source)


# ── Issue #2359：合法长文件名可以导入 ───────────────────────────────


def test_import_accepts_long_but_valid_filename(tmp_path: Path) -> None:
	source_dir = tmp_path / "src"
	source_dir.mkdir()
	name = "a" * 240 + ".bin"
	source = source_dir / name
	source.write_bytes(b"weights")

	imported = import_local_model(tmp_path / "data", source, "demo")

	assert Path(imported.path).name == name
	assert Path(imported.path).read_bytes() == b"weights"


# ── Issue #2360：base_url 错误不回显凭据 ────────────────────────────


@pytest.mark.parametrize(
	"value",
	[
		"https://api.example.com/v1?key=AIzaFAKESECRET bad",
		"https://api.example.com/v1?key=AIzaFAKESECRET" + "a" * 3000,
		"https://user:tokensecret@api.example.com/v1 bad",
	],
	ids=["invalid-space", "too-long", "userinfo"],
)
def test_base_url_errors_do_not_echo_credentials(value: str) -> None:
	with pytest.raises(AIConfigValueError) as excinfo:
		normalize_base_url(value)

	message = str(excinfo.value)
	assert "AIzaFAKESECRET" not in message
	assert "tokensecret" not in message


def test_base_url_query_branch_keeps_only_prefix() -> None:
	with pytest.raises(AIConfigValueError) as excinfo:
		normalize_base_url("https://api.example.com/v1?key=AIzaFAKESECRET")

	message = str(excinfo.value)
	assert "https://api.example.com/v1" in message
	assert "AIzaFAKESECRET" not in message


# ── Issue #2361：源路径末段为 .. / . 时按参数错误拒绝 ────────────────


def test_import_rejects_dotdot_source_name(tmp_path: Path) -> None:
	source_dir = tmp_path / "src" / "model"
	source_dir.mkdir(parents=True)
	data_dir = tmp_path / "data"

	with pytest.raises(LocalModelManifestError) as excinfo:
		import_local_model(data_dir, source_dir / "..", "demo")

	assert excinfo.value.code == "INVALID_PARAM"
	# 拒绝发生在创建任何目标目录之前。
	assert not (data_dir / "models" / "demo").exists()
