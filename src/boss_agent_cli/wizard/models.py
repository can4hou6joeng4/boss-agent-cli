"""Immutable workflow input and result models."""

from __future__ import annotations

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
		if not goal:
			raise WorkflowInputError("goal 不能为空")
		inputs = value.get("inputs", {})
		if not isinstance(inputs, Mapping):
			raise WorkflowInputError("inputs 必须是 JSON object")
		requested = value.get("requested_steps", ())
		if not isinstance(requested, (list, tuple)) or not all(isinstance(item, str) for item in requested):
			raise WorkflowInputError("requested_steps 必须是字符串数组")
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
