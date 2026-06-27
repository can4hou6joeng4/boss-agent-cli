#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DeepSeek Agent 示例 — 驱动 boss-agent-cli 自主求职。

用法:
    python run.py agent config --api-key <DEEPSEEK_KEY>
    python run.py login
    python examples/deepseek_agent.py "搜索北京 Python 岗位，过滤外包和低薪"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from boss_agent_cli.agent.runner import AgentRunner


def main() -> None:
	goal = sys.argv[1] if len(sys.argv) > 1 else (
		"搜索 Python 开发岗位，过滤外包和劳务派遣，列出推荐职位并说明原因"
	)
	data_dir = Path.home() / ".boss-agent"

	with AgentRunner(data_dir) as runner:
		result = runner.run_autonomous(
			goal,
			extra_rules="最低薪资 15K；排除外包、劳务派遣、异地远程",
		)

	print("=== Agent 总结 ===")
	print(result.get("summary", ""))
	if result.get("tool_calls"):
		print(f"\n共调用 {len(result['tool_calls'])} 次工具")


if __name__ == "__main__":
	main()
