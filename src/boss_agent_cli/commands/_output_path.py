"""`-o` 输出路径的前置校验：在发出任何平台请求之前暴露参数错误。

`export` / `chat --export` 把 `-o` 的问题留到抓完所有分页之后才由
`prepare_output_path` 抛出：`-o` 指向目录时，`count=40` 会先翻 5 页平台数据
（触发风控、消耗请求预算），再回一个与真正原因无关的信封。参数错误必须在
副作用之前暴露。

纯检查逻辑在 `services.output_path.inspect_output_path`（不依赖 click 与
display）；本模块只负责把检查结果转成统一信封。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from boss_agent_cli.services.output_path import OutputPathProblem, inspect_output_path

#: 判定 → (错误码, 恢复指引)。目标本身是目录 / 非普通文件属于参数错误；
#: 父路径不可用是本地 IO 契约。
_PROBLEM_CONTRACTS: dict[OutputPathProblem, tuple[str, str]] = {
	OutputPathProblem.IS_DIRECTORY: ("INVALID_PARAM", "在 --output 里给出完整文件名"),
	OutputPathProblem.NOT_REGULAR: ("INVALID_PARAM", "换一个普通文件路径后重试"),
	OutputPathProblem.PARENT_NOT_DIR: ("LOCAL_IO_ERROR", "换一个父目录存在且可写的路径后重试"),
}


def validate_output_path_or_emit(ctx: Any, command: str, raw: str | os.PathLike[str]) -> str | None:
	"""校验 `-o`；有问题时输出错误信封并返回 None，否则返回展开后的路径字符串。

	调用方应把它放在**任何平台请求或本地写入之前**。返回的路径仍需交给
	``prepare_output_path`` 去建父目录。
	"""
	from boss_agent_cli.display import handle_error_output

	target, problem = inspect_output_path(raw)
	if problem is None:
		return str(target)

	code, recovery_action = _PROBLEM_CONTRACTS[problem]
	if problem is OutputPathProblem.IS_DIRECTORY:
		message = f"{command} 的 -o / --output 需要文件路径，{str(target)!r} 是目录"
		recovery_action = f"{recovery_action}，例如 {_example(target / 'result.txt')}"
	elif problem is OutputPathProblem.NOT_REGULAR:
		message = f"{command} 的 -o / --output 需要普通文件路径，{str(target)!r} 不是普通文件"
	else:
		message = f"{command} 的 -o / --output 无法创建父目录，某级父路径不是目录: {str(target)!r}"

	handle_error_output(
		ctx,
		command,
		code=code,
		message=message,
		recovery_action=recovery_action,
	)
	return None


def _example(path: Path) -> str:
	"""示例路径：不替用户决定文件名，只把目录补成「目录/文件名」的样子。"""
	return str(path)
