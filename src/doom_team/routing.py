"""Dynamic capability routing: task requirements -> specialist selection.

The planner records WHAT capabilities each task requires; this layer decides
WHICH registered specialist handles them. Routing is deterministic and
auditable, uses a closed capability vocabulary, and fails closed: unknown
capabilities, empty requirements, or requirement sets no specialist fully
covers are routing errors - there is no silent generic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doom_team.domain import TaskContract

CAPABILITY_VOCABULARY = frozenset(
    {
        "backend.python",
        "database.sqlite",
        "testing",
        "documentation",
    }
)


class RoutingError(Exception):
    """Raised when no registered specialist can satisfy a task (fail closed)."""


@dataclass(frozen=True)
class SpecialistProfile:
    """A behaviorally distinct engineering specialist definition."""

    specialist_id: str
    supported_capabilities: tuple[str, ...]
    instruction_profile: str

    def covers(self, required: tuple[str, ...]) -> bool:
        return set(required) <= set(self.supported_capabilities)


SPECIALIST_REGISTRY: tuple[SpecialistProfile, ...] = (
    SpecialistProfile(
        specialist_id="python_backend",
        supported_capabilities=("backend.python",),
        instruction_profile=(
            "You are a Python backend implementation specialist. You build "
            "application and API code: request handling, routing, input "
            "validation, and error responses. You keep modules small, use "
            "only the standard library unless the task says otherwise, and "
            "make behavior observable through clear return values and status "
            "codes."
        ),
    ),
    SpecialistProfile(
        specialist_id="sqlite_persistence",
        supported_capabilities=("database.sqlite", "backend.python"),
        instruction_profile=(
            "You are a SQLite persistence specialist. You design minimal "
            "correct schemas, write parameterized queries only (never string "
            "interpolation), manage connections and transactions explicitly, "
            "and guarantee data survives process restarts."
        ),
    ),
    SpecialistProfile(
        specialist_id="test_engineer",
        supported_capabilities=("testing", "backend.python"),
        instruction_profile=(
            "You are a test engineering specialist. You write executable "
            "automated tests that exercise real behavior: happy paths, edge "
            "cases, and failure modes. Tests must be deterministic, isolated "
            "via temporary state, and runnable with python -m pytest with no "
            "extra installs."
        ),
    ),
    SpecialistProfile(
        specialist_id="technical_writer",
        supported_capabilities=("documentation",),
        instruction_profile=(
            "You are a technical documentation specialist. You write a "
            "README a newcomer can follow verbatim on a fresh checkout: "
            "install, run, and test instructions with exact commands, plus a "
            "short API usage section with concrete examples."
        ),
    ),
)


@dataclass(frozen=True)
class RoutingDecision:
    """Auditable, serializable record of one routing selection."""

    task_id: str
    required_capabilities: tuple[str, ...]
    specialist_id: str
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "required_capabilities": list(self.required_capabilities),
            "specialist_id": self.specialist_id,
            "rationale": self.rationale,
        }


def _covering_profiles(
    required: tuple[str, ...], registry: tuple[SpecialistProfile, ...]
) -> list[SpecialistProfile]:
    """Specialists that cover the WHOLE requirement set. The one routing rule."""
    return [profile for profile in registry if profile.covers(required)]


def capability_is_routable(
    required: tuple[str, ...] | list[str],
    registry: tuple[SpecialistProfile, ...] = SPECIALIST_REGISTRY,
) -> bool:
    """True when :func:`route_task` could select a specialist for this set.

    Same three conditions route_task applies, in one place, so a caller that
    needs to know routability in advance cannot drift from the router.
    """
    wanted = tuple(required)
    if not wanted:
        return False
    if set(wanted) - CAPABILITY_VOCABULARY:
        return False
    return bool(_covering_profiles(wanted, registry))


def routable_capability_sets(
    registry: tuple[SpecialistProfile, ...] = SPECIALIST_REGISTRY,
) -> tuple[tuple[str, ...], ...]:
    """Each specialist's full coverage set, deduplicated and sorted.

    Membership in the vocabulary is not enough: a requirement set must be
    covered by ONE specialist, so a set spanning two of them cannot be routed
    and must never be planned. Because coverage is a subset test, a task is
    routable exactly when its requirements are a non-empty subset of one of
    these sets.
    """
    seen = {tuple(sorted(profile.supported_capabilities)) for profile in registry}
    return tuple(sorted(seen))


def capability_contract_text(
    registry: tuple[SpecialistProfile, ...] = SPECIALIST_REGISTRY,
) -> str:
    """The routing contract, rendered for whoever must plan within it.

    Generated from the live registry and vocabulary, so a planner shown this
    text is shown exactly what the router will accept - not a copy that can
    fall out of date.
    """
    tokens = ", ".join(sorted(CAPABILITY_VOCABULARY))
    combos = "\n".join(
        f"  - {', '.join(combo)}" for combo in routable_capability_sets(registry)
    )
    return (
        f"AVAILABLE CAPABILITIES (use these exact tokens and nothing else): {tokens}.\n"
        "ROUTING RULE: one specialist executes a whole task, so a task's "
        "required_capabilities must be a non-empty SUBSET of one of these "
        "specialist coverage sets:\n"
        f"{combos}\n"
        "A task requiring capabilities from two different sets cannot be "
        "routed; split it into separate tasks instead.\n"
        "Never invent capability names, synonyms, or variants of these tokens. "
        "General application programming - command-line handling, HTTP "
        "serving, parsing, validation, error handling - is backend.python; "
        "there is no separate capability for it."
    )


def route_task(
    task: TaskContract,
    registry: tuple[SpecialistProfile, ...] = SPECIALIST_REGISTRY,
) -> RoutingDecision:
    """Deterministically select the specialist for one task (fail closed)."""
    required = task.required_capabilities
    if not required:
        raise RoutingError(
            f"task {task.id} declares no required capabilities; nothing to route"
        )
    unknown = sorted(set(required) - CAPABILITY_VOCABULARY)
    if unknown:
        raise RoutingError(
            f"task {task.id} requires unknown capabilities: {unknown}"
        )
    candidates = _covering_profiles(required, registry)
    if not candidates:
        raise RoutingError(
            f"no registered specialist covers all capabilities {sorted(required)} "
            f"required by task {task.id}"
        )
    # Deterministic: the most specialized full-cover specialist wins; ties
    # break lexicographically. Never a silent generic fallback - a selected
    # specialist provably supports every required capability.
    selected = min(
        candidates,
        key=lambda profile: (len(profile.supported_capabilities), profile.specialist_id),
    )
    return RoutingDecision(
        task_id=task.id,
        required_capabilities=required,
        specialist_id=selected.specialist_id,
        rationale=(
            f"supports all required capabilities {sorted(required)} with the "
            f"most specialized full-cover profile "
            f"({len(selected.supported_capabilities)} supported)"
        ),
    )


def specialist_by_id(
    specialist_id: str,
    registry: tuple[SpecialistProfile, ...] = SPECIALIST_REGISTRY,
) -> SpecialistProfile:
    """Look up a registered specialist; raises :class:`RoutingError`."""
    for profile in registry:
        if profile.specialist_id == specialist_id:
            return profile
    raise RoutingError(f"unknown specialist: {specialist_id}")
