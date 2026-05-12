"""issue #217 侦察脚本 dry-run 路径回归。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "probe_recruiter_chat_frontend.py"


def _load_script_module():
	spec = importlib.util.spec_from_file_location("probe_recruiter_chat_frontend", _SCRIPT)
	assert spec and spec.loader, "spec 加载失败"
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_dry_run_emits_report_skeleton_and_js_payloads(capsys):
	"""--dry-run 路径：stdout 输出 report skeleton，stderr 输出 JS payloads。"""
	module = _load_script_module()
	exit_code = module.main(["--dry-run"])
	assert exit_code == 0
	captured = capsys.readouterr()

	parsed = json.loads(captured.out)
	assert parsed["issue"].endswith("issues/217")
	assert "websocket" in parsed
	assert parsed["friend_id"] is None
	assert parsed["global_chat_keys"] == []

	assert "WebSocket spy" in captured.err
	assert "__hf_ws_probe__" in captured.err
	assert "Vue/Vuex/Pinia probe" in captured.err


def test_dry_run_friend_id_passthrough(capsys):
	"""--dry-run 时也保留 friend_id 信息，方便用户审阅命令是否填对。"""
	module = _load_script_module()
	exit_code = module.main(["--dry-run", "--friend-id", "99999"])
	assert exit_code == 0
	captured = capsys.readouterr()
	parsed = json.loads(captured.out)
	assert parsed["friend_id"] == 99999
