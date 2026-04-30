"""验证 boss resume init 的 hint 引导用户使用 boss me 拉取真实简历。"""
import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_resume_init_hints_include_boss_me(tmp_path: Path) -> None:
	"""resume init 完成后应在 hints.next_actions 提示用户用 boss me 拉真实简历。"""
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "resume", "init", "--name", "test"],
	)
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	next_actions = envelope.get("hints", {}).get("next_actions", [])
	assert any("boss me" in action for action in next_actions), (
		f"期望 hint 提示用户走 boss me 拉真实简历，实际 next_actions={next_actions}"
	)
