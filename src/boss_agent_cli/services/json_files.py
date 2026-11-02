"""本地 JSON 状态的原子写入。

`Path.write_text` 写到一半被中断（Ctrl-C、进程被杀、磁盘写满）会留下半截文件，
下一次读取直接失败；本项目的本地状态（config.json、简历、模型登记表、索引缓存）
都依赖整份 JSON 才可用，所以统一走「同目录临时文件 + os.replace」。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(
	path: Path,
	value: Any,
	*,
	indent: int | None = 2,
	trailing_newline: bool = False,
) -> None:
	"""原子替换写入 JSON 文件；失败时保留原文件，不留下临时文件。"""
	path.parent.mkdir(parents=True, exist_ok=True)
	fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
	temporary = Path(temporary_name)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as handle:
			json.dump(value, handle, ensure_ascii=False, indent=indent)
			if trailing_newline:
				handle.write("\n")
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary, path)
	finally:
		temporary.unlink(missing_ok=True)
