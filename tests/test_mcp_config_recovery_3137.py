"""MCP config 写中断恢复指引必须停留在本地配置（Issue #3137）。"""

import pytest

from boss_agent_cli.mcp_server import _parse_cli_output, _write_interrupt_recovery


@pytest.mark.parametrize(
	"argv",
	[("config", "set", "log_level", "debug"), ("config", "reset", "log_level")],
)
def test_config_recovery_stays_local(argv):
	message = _write_interrupt_recovery(argv)
	assert "boss_config list" in message
	assert "不产生平台写入" in message
	assert "checkpoint" not in message
	assert "平台实际结果" not in message


def test_bad_cli_output_uses_local_config_recovery():
	result = _parse_cli_output(
		("config", "set", "log_level", "debug"), "not a JSON envelope\n", "", returncode=0
	)
	action = result["error"]["recovery_action"]
	assert "boss_config list" in action
	assert "不产生平台写入" in action
	assert "checkpoint" not in action
