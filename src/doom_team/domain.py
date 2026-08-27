"""Core domain model for Doom Team.

These dataclasses capture the essential entities and their invariants
without depending on any infrastructure or orchestration engine.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# THE task-identifier lexical contract, shared by the domain, the planner
# prompt/validation, and the deliverable workspace layer. The pattern is the
# deliverable layer's historical workspace-safety rule adopted unchanged as
# the single source of truth: a task id becomes a workspace directory name
# and a git branch segment, so the vocabulary must stay filesystem- and
# ref-safe. A plan accepted anywhere upstream of a workspace must therefore
# satisfy exactly this rule - two layers carrying separate regexes is how a
# valid-looking plan dies at its first workspace clone.
TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")

# Human-readable form of TASK_ID_PATTERN, for prompts and error messages.
# Keep the two in lockstep; tests pin their agreement.
TASK_ID_RULE = (
    "lowercase ASCII letters, digits, '.', '_' or '-' only, starting with a "
    "lowercase letter or digit, at most 81 characters"
)


def task_id_is_valid(task_id: str) -> bool:
    """Whether an identifier satisfies the canonical task-id contract."""
    return bool(TASK_ID_PATTERN.fullmatch(task_id))


class AcceptanceState(Enum):
    UNASSESSED = "UNASSESSED"
    CANDIDATE_READY = "CANDIDATE_READY"
    QA_ACCEPTED = "QA_ACCEPTED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    OBJECTIVE_ACCEPTED = "OBJECTIVE_ACCEPTED"
    WAITING_FOR_FINAL_APPROVAL = "WAITING_FOR_FINAL_APPROVAL"
    RELEASE_AUTHORIZED = "RELEASE_AUTHORIZED"


class SupervisorDecision(Enum):
    """Explicit terminal decisions from the deterministic Supervisor."""

    BLOCKED = "BLOCKED"
    READY_FOR_FINAL_APPROVAL = "READY_FOR_FINAL_APPROVAL"
    RELEASE_AUTHORIZED = "RELEASE_AUTHORIZED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


@dataclass(frozen=True)
class Objective:
    id: str
    description: str

    def __post_init__(self) -> None:
        id_ = self.id.strip()
        desc = self.description.strip()
        if not id_:
            raise ValueError("Objective id must not be empty")
        if not desc:
            raise ValueError("Objective description must not be empty")
        object.__setattr__(self, "id", id_)
        object.__setattr__(self, "description", desc)


@dataclass(frozen=True)
class TaskContract:
    id: str
    objective_id: str
    instruction: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        id_ = self.id.strip()
        obj_id = self.objective_id.strip()
        instr = self.instruction.strip()
        if not id_:
            raise ValueError("Task id must not be empty")
        if not task_id_is_valid(id_):
            raise ValueError(
                f"Task id {id_!r} is invalid: task ids use {TASK_ID_RULE}"
            )
        if not obj_id:
            raise ValueError("Objective id must not be empty")
        if not instr:
            raise ValueError("Instruction must not be empty")

        if not self.acceptance_criteria:
            raise ValueError("Acceptance criteria must not be empty")
        criteria = tuple(c.strip() for c in self.acceptance_criteria)
        if any(not c for c in criteria):
            raise ValueError("Acceptance criteria must not be empty strings")

        deps = tuple(d.strip() for d in self.dependencies)
        if any(not d for d in deps):
            raise ValueError("Dependencies must not be empty strings")
        bad_deps = sorted(d for d in deps if not task_id_is_valid(d))
        if bad_deps:
            raise ValueError(
                f"Dependencies {bad_deps} are invalid: task ids use {TASK_ID_RULE}"
            )
        if id_ in deps:
            raise ValueError("Task cannot depend on itself")
        if len(set(deps)) != len(deps):
            raise ValueError("Dependencies must be unique")

        # Capability identifiers only record WHAT a task requires; specialist
        # selection semantics belong to the routing layer, not the domain.
        capabilities = tuple(c.strip() for c in self.required_capabilities)
        if any(not c for c in capabilities):
            raise ValueError("Required capabilities must not be empty strings")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("Required capabilities must be unique")
        if len(capabilities) > 8:
            raise ValueError("Required capabilities cannot exceed 8 entries")

        criteria = tuple(sorted(criteria))
        deps = tuple(sorted(deps))
        capabilities = tuple(sorted(capabilities))
        object.__setattr__(self, "id", id_)
        object.__setattr__(self, "objective_id", obj_id)
        object.__setattr__(self, "instruction", instr)
        object.__setattr__(self, "acceptance_criteria", criteria)
        object.__setattr__(self, "dependencies", deps)
        object.__setattr__(self, "required_capabilities", capabilities)


@dataclass(frozen=True)
class Mission:
    id: str
    objective: Objective
    tasks: tuple[TaskContract, ...]

    def __post_init__(self) -> None:
        mission_id = self.id.strip()
        if not mission_id:
            raise ValueError("Mission id must not be empty")

        if not self.tasks:
            raise ValueError("Mission must have at least one task")

        # Validate objective_id consistency
        for task in self.tasks:
            if task.objective_id != self.objective.id:
                raise ValueError(
                    f"Task {task.id} objective_id does not match mission objective"
                )

        # Validate unique task ids
        task_ids = [t.id for t in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Task ids must be unique within a mission")

        # Validate dependency existence
        task_id_set = set(task_ids)
        for task in self.tasks:
            for dep in task.dependencies:
                if dep not in task_id_set:
                    raise ValueError(f"Task {task.id} depends on unknown task {dep}")

        # Detect cycles using DFS
        adj = {t.id: set(t.dependencies) for t in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visiting:
                raise ValueError("Cyclic dependency detected")
            if node in visited:
                return
            visiting.add(node)
            for dep in adj[node]:
                dfs(dep)
            visiting.remove(node)
            visited.add(node)

        for task in self.tasks:
            dfs(task.id)

        # Canonicalize: sort tasks by id
        tasks = tuple(sorted(self.tasks, key=lambda t: t.id))
        object.__setattr__(self, "id", mission_id)
        object.__setattr__(self, "tasks", tasks)


@dataclass(frozen=True)
class Attempt:
    id: str
    task_id: str
    recovery_epoch: int = 0


@dataclass(frozen=True)
class RecoveryEpoch:
    attempt: Attempt
    semantic_retries_left: int


@dataclass(frozen=True)
class Candidate:
    id: str
    task_id: str
    attempt: Attempt
    baseline_commit: str
    candidate_commit: str
    workspace_id: str


@dataclass(frozen=True)
class Evidence:
    id: str
    candidate: Candidate
    objective_id: str
    mission_id: str
    producer_role: str
    producer_run: str
    command: str
    artifacts: tuple[str, ...]
    actual_model: str
    actual_runtime: str
    timestamp: datetime.datetime
    digest: str


@dataclass(frozen=True)
class Acceptance:
    candidate: Candidate
    state: AcceptanceState = AcceptanceState.UNASSESSED


@runtime_checkable
class ApprovalPort(Protocol):
    def request_approval(self, action: str, context: dict[str, Any]) -> bool:
        """Request human approval for an irreversible action."""


@dataclass
class ApprovalBoundary:
    """Gate irreversible actions behind explicit human approval.

    The autonomous path must never call a mutation without first obtaining
    a positive approval through this boundary.
    """

    approval_port: ApprovalPort | None = None

    def request(self, action: str, context: dict[str, Any] | None = None) -> bool:
        """Return True only if a positive human approval is recorded."""
        if self.approval_port is None:
            return False
        return self.approval_port.request_approval(action, context or {})
