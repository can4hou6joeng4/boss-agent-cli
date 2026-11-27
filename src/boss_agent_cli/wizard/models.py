"""Immutable workflow input and result models."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class WorkflowStatus(str, Enum):
	PENDING = "pending"
	RUNNING = "running"
	WAITING_INPUT = "waiting_input"
	COMPLETED = "completed"
	FAILED = "failed"
	STOPPED = "stopped"


class WorkflowInputError(ValueError):
	"""Raised when wizard/headless input cannot form a workflow plan."""


def _reject_lone_surrogates(text: str, path: str) -> None:
	"""孤立代理字符无法按 UTF-8 编码（Issue #2058）。

	``json.loads`` 会把 ``\\ud800`` 还原成孤立代理并放行；随后 sqlite3 绑参时抛
	``UnicodeEncodeError``（不是 ``sqlite3.DatabaseError``，不经本地 IO 翻译），
	命令层把它归成 ``LOCAL_IO_ERROR``，让操作者去查磁盘权限——实际是输入非法。
	入口处直接拒绝，与 MCP 侧 ``_unsafe_argv_reason``（#1236/#1237）同口径。
	"""
	for char in text:
		if 0xD800 <= ord(char) <= 0xDFFF:
			raise WorkflowInputError(
				f"{path} 含无法编码的代理字符（U+{ord(char):04X}）；请检查输入 JSON 的转义"
			)


#: 输入结构校验的递归深度上限：真实 workflow 输入远达不到，超过后
#: 递归校验会在解释器递归上限附近抛 RecursionError 并逃出信封边界（Issue #2186）。
_MAX_INPUT_DEPTH = 200


def ensure_storable_inputs(value: Any, path: str = "inputs", depth: int = 0) -> None:
	"""递归拒绝 NaN / Infinity（Issue #1883）、孤立代理字符（Issue #2058）
	与超出解释器位数上限的整数（Issue #3085）。

	``json.loads`` 默认接受 ``NaN`` / ``Infinity``，而 ``nan != nan`` 恒真：
	含非有限数值的 plan 一旦落库，恢复时永远比对不相等，run 永久不可恢复；
	非标准 JSON 常量也不符合本仓库「严格 JSON」的输出契约。孤立代理字符同理：
	``ensure_ascii=False`` 写 JSON 列时保留裸代理，sqlite3 绑参直接抛编码错误。
	入口处一次拒绝，CLI、MCP 与 Python API 共享同一条边界。

	嵌套深度按显式上限拒绝（Issue #2186）：深嵌套输入会让这里的递归先于
	``json.loads`` 的边界抛 ``RecursionError``，命令层拿不到信封。上限给出
	确定、可复现的 ``INVALID_PARAM`` 诊断，不依赖解释器实现。
	"""
	if depth > _MAX_INPUT_DEPTH:
		raise WorkflowInputError(
			f"{path} 嵌套超过 {_MAX_INPUT_DEPTH} 层；workflow 输入不支持如此深的嵌套"
		)
	if isinstance(value, float) and not math.isfinite(value):
		raise WorkflowInputError(f"{path} 不能包含 NaN 或 Infinity；请改用有限数值")
	if isinstance(value, str):
		_reject_lone_surrogates(value, path)
	elif isinstance(value, Mapping):
		for key, item in value.items():
			item_path = f"{path}.{key}"
			# JSON 对象键只能是字符串。数字 / 布尔 / null 会被 json.dumps 静默转成
			# 字符串，但混合类型键在 sort_keys=True 排序时抛 TypeError：落点跑到
			# WorkflowStore.create，命令层只能兜成 INTERNAL_ERROR（Issue #2420 /
			# #2485）。入口统一拒绝，CLI、MCP 与 Python API 共享同一条诊断。
			if not isinstance(key, str):
				raise WorkflowInputError(
					f"{item_path} 的键类型 {type(key).__name__} 不能落成 JSON；"
					"对象键只支持字符串"
				)
			# 字符串键的代理字符由递归校验统一处理。
			ensure_storable_inputs(key, item_path, depth + 1)
			ensure_storable_inputs(item, item_path, depth + 1)
	elif isinstance(value, (list, tuple)):
		for index, item in enumerate(value):
			ensure_storable_inputs(item, f"{path}[{index}]", depth + 1)
	elif isinstance(value, int) and not isinstance(value, bool):
		# CPython 限制 int → 十进制字符串的上限，json.dumps 走同一个转换：
		# 超限整数在这里放行，落库时才抛裸 ValueError（命令层兜成 INTERNAL_ERROR）。
		# 用同一个 str() 判定（捕获其 ValueError）而非自算位数，保证与落库口径
		# 完全一致；上限为 0 表示解释器关闭了限制（Issue #3085）。
		if sys.get_int_max_str_digits():
			try:
				str(value)
			except ValueError:
				raise WorkflowInputError(
					f"{path} 的整数超过 {sys.get_int_max_str_digits()} 位，无法落成 JSON"
				) from None
	elif value is not None and not isinstance(value, (bool, int, float)):
		# JSON 原生类型之外的值（bytes / set / Decimal / 自定义对象）无法落到
		# 断点的 JSON 列：WorkflowStore.create 的 json.dumps 会抛 TypeError，
		# 落点在本边界之外，命令层只有通用兜底（Issue #2420）。
		raise WorkflowInputError(
			f"{path} 不是可存储为 JSON 的值（{type(value).__name__}）；"
			"只支持 null / 布尔 / 数字 / 字符串 / 数组 / 对象"
		)


def count_or_none(value: Any) -> int | None:
	"""展示层读取持久化计数的容错入口：缺失或漂移返回 None。

	``workflow_runs.last_result`` 是历史落库的 JSON；``jobs_seen`` 被写成文本、
	数组或负数时，渲染器的裸 ``int()`` 会让整个状态面板 traceback（渲染没有
	命令层的错误出口兜底）。展示问题不得中断主流程（Issue #1212）。
	"""
	if value is None or isinstance(value, bool):
		return None
	try:
		parsed = int(value)
	except (TypeError, ValueError, OverflowError):
		return None
	return parsed if parsed >= 0 else None


@dataclass(frozen=True)
class WizardInput:
	role: str
	platform: str
	goal: str
	inputs: Mapping[str, Any] = field(default_factory=dict)
	requested_steps: tuple[str, ...] = ()
	mode: str = "headless"

	@classmethod
	def from_mapping(cls, value: Mapping[str, Any], *, mode: str = "headless") -> "WizardInput":
		role = str(value.get("role") or "").strip()
		platform = str(value.get("platform") or "").strip()
		goal = str(value.get("goal") or "").strip()
		if role not in {"candidate", "recruiter"}:
			raise WorkflowInputError("role 必须是 candidate 或 recruiter")
		if not platform:
			raise WorkflowInputError("platform 不能为空")
		_reject_lone_surrogates(platform, "platform")
		if not goal:
			raise WorkflowInputError("goal 不能为空")
		_reject_lone_surrogates(goal, "goal")
		inputs = value.get("inputs", {})
		if not isinstance(inputs, Mapping):
			raise WorkflowInputError("inputs 必须是 JSON object")
		ensure_storable_inputs(inputs)
		requested: Any = ()
		if "requested_steps" in value:
			requested = value["requested_steps"]
			if not isinstance(requested, (list, tuple)) or not all(isinstance(item, str) for item in requested):
				raise WorkflowInputError("requested_steps 必须是字符串数组")
			for index, step_name in enumerate(requested):
				_reject_lone_surrogates(step_name, f"requested_steps[{index}]")
			if not requested:
				# 空数组不能折叠成「执行 goal 的全部步骤」：goal 里可能包含真实平台
				# 写操作（如 candidate_greet），调用方显式传空子集的预期是不执行任何
				# 步骤；只有省略字段才表示全量执行（Issue #1236）。
				raise WorkflowInputError(
					"requested_steps 不能是空数组；省略该字段才表示执行 goal 的全部步骤"
				)
		return cls(
			role=role,
			platform=platform,
			goal=goal,
			inputs=dict(inputs),
			requested_steps=tuple(requested),
			mode=mode,
		)


@dataclass(frozen=True)
class WorkflowPlan:
	role: str
	platform: str
	goal: str
	inputs: Mapping[str, Any]
	requested_steps: tuple[str, ...]
	mode: str


@dataclass(frozen=True)
class StepResult:
	data: Mapping[str, Any]
	status: WorkflowStatus = WorkflowStatus.COMPLETED
	next_action: str | None = None
	artifacts: tuple[str, ...] = ()
	# 面向真人操作者的自然语言指引（需要离开终端完成的动作）。
	# 与 next_action（面向 Agent 的命令）职责互斥，见 schema conventions.hints。
	operator_actions: tuple[str, ...] = ()

	def to_dict(self) -> dict[str, Any]:
		return {
			"data": dict(self.data),
			"status": self.status.value,
			"next_action": self.next_action,
			"artifacts": list(self.artifacts),
			"operator_actions": list(self.operator_actions),
		}
