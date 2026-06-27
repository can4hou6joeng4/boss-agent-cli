#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
直接运行 boss-agent-cli 入口
无需安装 pip install -e .
"""

import sys
from pathlib import Path

# 把 src 目录加入到 Python 路径
project_root = Path(__file__).parent
src_dir = project_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# 导入并运行 CLI
from boss_agent_cli.main import cli

if __name__ == "__main__":
    cli()
