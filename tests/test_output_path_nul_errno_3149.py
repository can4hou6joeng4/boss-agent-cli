"""含 NUL 的输出路径错误必须保留 errno、原因与 filename（Issue #3149）。"""

import errno
from pathlib import Path

import pytest

from boss_agent_cli.services.output_path import inspect_output_path, prepare_output_path


@pytest.mark.parametrize("call", [inspect_output_path, prepare_output_path], ids=["inspect", "prepare"])
def test_nul_target_reports_the_underlying_reason(tmp_path: Path, call) -> None:
	target = tmp_path / "nul\0name.md"

	with pytest.raises(OSError) as caught:
		call(target)

	assert caught.value.errno == errno.EINVAL
	assert caught.value.filename == str(target)
	message = str(caught.value)
	assert "无法读取路径状态" in message
	# Linux / macOS 对 NUL 的 strerror 文案不同，只断言稳定的共同片段。
	assert "embedded null" in message
