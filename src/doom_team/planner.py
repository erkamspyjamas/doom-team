"""Objective planning: natural language objective -> validated Mission.

The planner is the first Doom Team intelligence path. It asks the configured
inference client for a STRICT machine-readable decomposition of a software
objective into tasks with dependencies and required capabilities, then
validates the result fail-closed through the existing domain invariants:
malformed planner output can never enter runtime state.

The planner decides WHAT work is required; specialist selection belongs to
the routing layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from doom_team.domain import Mission, Objective, TaskContract
from doom_team.inference import InferenceError
from doom_team.ports import InferencePort

MAX_PLAN_TASKS = 10
MAX_DESCRIPTION_CHARS = 2000
MAX_DEPENDENCIES_PER_TASK = 8
MAX_IDENTIFIER_CHARS = 120
MAX_CRITERION_CHARS = 500
MAX_CRITERIA_PER_TASK = 8

PLANNER_SYSTEM_PROFILE = (
    "You are the Doom Team mission planner. Decompose one software objective "
    "into a small dependency-ordered task plan. You never write implementation "
    "code and never invent requirements beyond the objective. Return STRICT "
    "JSON only - no markdown, no prose - matching exactly: "
    '{"tasks": [{"task_id": str, "description": str, '
    '"dependencies": [str, ...], "required_capabilities": [str, ...], '
    '"acceptance_criteria": [str, ...]}]}. '
    f"Use at most {MAX_PLAN_TASKS} tasks. Task ids must be unique short "
    "slugs. Dependencies may only reference task ids in the same plan and "
    "must form an acyclic graph. Every task declares at least one required "
    "capability from the routing vocabulary and at least one concrete, "
    "checkable acceptance criterion."
)


class PlanningError(Exception):
    """Raised when the planner cannot produce a valid plan (fail closed)."""


@dataclass(frozen=True)
class PlannerConfig:
    """Bounded planner limits (first-slice conservative defaults)."""

    max_tasks: int = MAX_PLAN_TASKS
    max_description_chars: int = MAX_DESCRIPTION_CHARS
    max_dependencies: int = MAX_DEPENDENCIES_PER_TASK


class Planner:
    """LLM-backed planner producing a validated :class:`Mission`."""

    def __init__(
        self, inference: InferencePort, *, config: PlannerConfig | None = None
    ) -> None:
        self._inference = inference
        self._config = config or PlannerConfig()

    def plan(self, objective_text: str) -> Mission:
        """Plan the objective; raises :class:`PlanningError` fail-closed."""
        text = objective_text.strip()
        if not text:
            raise PlanningError("objective must not be empty")
        objective = Objective(id=_objective_id(text), description=text)
        try:
            response = self._inference.complete(
                [
                    {"role": "system", "content": PLANNER_SYSTEM_PROFILE},
                    {"role": "user", "content": text},
                ]
            )
        except InferenceError as exc:
            # Provider failures stay fail-closed planning failures, never raw
            # tracebacks or misclassified configuration errors.
            raise PlanningError(f"inference provider failed: {exc}") from exc
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise PlanningError("planner returned no content")
        return self._parse_plan(objective, content)

    def _parse_plan(self, objective: Objective, content: str) -> Mission:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PlanningError(f"planner output is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise PlanningError("planner output must be a JSON object")
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanningError("planner output must contain a non-empty tasks list")
        if len(raw_tasks) > self._config.max_tasks:
            raise PlanningError(
                f"plan exceeds the {self._config.max_tasks}-task bound"
            )
        contracts: list[TaskContract] = []
        for index, raw in enumerate(raw_tasks):
            contracts.append(self._parse_task(objective, index, raw))
        try:
            return Mission(id=f"mission-{objective.id}", objective=objective, tasks=tuple(contracts))
        except ValueError as exc:
            raise PlanningError(f"plan violates mission invariants: {exc}") from exc

    def _parse_task(
        self, objective: Objective, index: int, raw: Any
    ) -> TaskContract:
        if not isinstance(raw, dict):
            raise PlanningError(f"tasks[{index}] must be a JSON object")
        # STRICT contract: every schema key must be present explicitly; a
        # silently defaulted "dependencies" would drop ordering constraints.
        for key in (
            "task_id",
            "description",
            "dependencies",
            "required_capabilities",
            "acceptance_criteria",
        ):
            if key not in raw:
                raise PlanningError(f"tasks[{index}] is missing the {key} field")
        task_id = raw["task_id"]
        description = raw["description"]
        dependencies = raw["dependencies"]
        capabilities = raw["required_capabilities"]
        criteria = raw["acceptance_criteria"]
        if not isinstance(task_id, str) or isinstance(task_id, bool):
            raise PlanningError(f"tasks[{index}].task_id must be a string")
        if len(task_id) > MAX_IDENTIFIER_CHARS:
            raise PlanningError(f"tasks[{index}].task_id exceeds the length bound")
        if not isinstance(description, str):
            raise PlanningError(f"tasks[{index}].description must be a string")
        if len(description) > self._config.max_description_chars:
            raise PlanningError(f"tasks[{index}].description exceeds the length bound")
        for name, value in (
            ("dependencies", dependencies),
            ("required_capabilities", capabilities),
            ("acceptance_criteria", criteria),
        ):
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise PlanningError(f"tasks[{index}].{name} must be a list of strings")
        if len(dependencies) > self._config.max_dependencies:
            raise PlanningError(f"tasks[{index}] exceeds the dependency bound")
        for name, values, bound in (
            ("dependencies", dependencies, MAX_IDENTIFIER_CHARS),
            ("required_capabilities", capabilities, MAX_IDENTIFIER_CHARS),
            ("acceptance_criteria", criteria, MAX_CRITERION_CHARS),
        ):
            if any(len(item) > bound for item in values):
                raise PlanningError(
                    f"tasks[{index}].{name} entries exceed the length bound"
                )
        if len(criteria) > MAX_CRITERIA_PER_TASK:
            raise PlanningError(f"tasks[{index}] exceeds the acceptance-criteria bound")
        if not capabilities:
            raise PlanningError(
                f"tasks[{index}] must declare at least one required capability"
            )
        try:
            return TaskContract(
                id=task_id,
                objective_id=objective.id,
                instruction=description,
                acceptance_criteria=tuple(criteria),
                dependencies=tuple(dependencies),
                required_capabilities=tuple(capabilities),
            )
        except ValueError as exc:
            raise PlanningError(f"tasks[{index}] is invalid: {exc}") from exc


def _objective_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
