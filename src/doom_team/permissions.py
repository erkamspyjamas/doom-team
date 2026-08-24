"""Deterministic capability and permission policy.

Pure, testable paths for role/tool/workspace authorization. Dangerous
irreversible actions are denied by default and require human approval
through the ApprovalBoundary before any mutation may proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Capability(Enum):
    """Distinct tool/mutation operations supported by Doom Team."""

    # Reversible local-workspace actions
    EDIT = "EDIT"
    RUN_VERIFICATION = "RUN_VERIFICATION"
    COMMIT = "COMMIT"
    SNAPSHOT = "SNAPSHOT"

    # Irreversible / shared-state / dangerous actions (human-gated)
    PUSH = "PUSH"
    MERGE = "MERGE"
    DEPLOY = "DEPLOY"
    DESTRUCTIVE_DB = "DESTRUCTIVE_DB"


class Role(Enum):
    """Roles recognized by the Doom Team autonomous workflow."""

    DEV = "DEV"
    QA = "QA"
    REVIEWER = "REVIEWER"
    SUPERVISOR = "SUPERVISOR"


class Decision(Enum):
    """The deterministic outcome of a policy evaluation."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


@dataclass(frozen=True)
class PolicyDecision:
    """Immutable policy outcome for a role and capability."""

    role: Role
    capability: Capability
    decision: Decision
    reason: str


_DANGEROUS = frozenset(
    {
        Capability.PUSH,
        Capability.MERGE,
        Capability.DEPLOY,
        Capability.DESTRUCTIVE_DB,
    }
)

_ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.DEV: frozenset(
        {
            Capability.EDIT,
            Capability.RUN_VERIFICATION,
            Capability.COMMIT,
            Capability.SNAPSHOT,
        }
    ),
    Role.QA: frozenset({Capability.RUN_VERIFICATION}),
    Role.REVIEWER: frozenset({Capability.RUN_VERIFICATION}),
    Role.SUPERVISOR: frozenset({Capability.RUN_VERIFICATION}),
}

# Capabilities that operate on a target path and must be within workspace scope.
_SCOPED_CAPABILITIES = frozenset({Capability.EDIT})


def _is_within(root: str, target: str) -> bool:
    """Return True only if target path is resolved inside the root path."""
    try:
        root_path = Path(root).resolve()
        target_path = Path(target).resolve()
    except OSError:
        return False
    return target_path.is_relative_to(root_path)


def authorize(
    role: Role,
    capability: Capability,
    workspace_root: str | None = None,
    target: str | None = None,
) -> PolicyDecision:
    """Evaluate whether a role is allowed to invoke a capability.

    Dangerous irreversible capabilities are never auto-approved; they return
    REQUIRES_APPROVAL and must pass the human ApprovalBoundary before any
    mutation. Workspace-scoped capabilities additionally require the target
    path to be inside the workspace root.
    """
    if capability in _DANGEROUS:
        return PolicyDecision(
            role=role,
            capability=capability,
            decision=Decision.REQUIRES_APPROVAL,
            reason=f"{capability.value} requires human approval",
        )

    allowed = _ROLE_CAPABILITIES[role]
    if capability not in allowed:
        return PolicyDecision(
            role=role,
            capability=capability,
            decision=Decision.DENIED,
            reason=f"{role.value} lacks {capability.value}",
        )

    if capability in _SCOPED_CAPABILITIES:
        if workspace_root is None or target is None:
            return PolicyDecision(
                role=role,
                capability=capability,
                decision=Decision.DENIED,
                reason="workspace scope requires root and target",
            )
        if not _is_within(workspace_root, target):
            return PolicyDecision(
                role=role,
                capability=capability,
                decision=Decision.DENIED,
                reason="target outside workspace scope",
            )

    return PolicyDecision(
        role=role,
        capability=capability,
        decision=Decision.ALLOWED,
        reason="allowed",
    )
