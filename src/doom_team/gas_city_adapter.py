"""Gas City orchestration adapter.

Implements OrchestrationPort without leaking Gas City internals into the
domain. The adapter talks to a GasCityClient protocol and persists mapping
between Doom run ids and Gas City run ids in a durable store so that work
can be recovered after a restart. Enqueue is idempotent: re-submitting with
the same Doom run id reuses the existing mapping and never duplicates the
remote run.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from doom_team.ports import OrchestrationPort


class RunState:
    """Observed state of an orchestrated run."""

    def __init__(self, run_id: str, status: str, dependencies: tuple[str, ...] = ()) -> None:
        self.run_id = run_id
        self.status = status
        self.dependencies = dependencies

    def __repr__(self) -> str:
        return f"RunState(run_id={self.run_id!r}, status={self.status!r})"


@runtime_checkable
class GasCityClient(Protocol):
    """Minimal interface to Gas City.

    This is the only coupling the adapter has to Gas City; the domain core
    never sees these types.
    """

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        """Enqueue a task and return a Gas City run id."""

    def observe(self, run_id: str) -> Any:
        """Return an object with a 'status' attribute and optional 'dependencies'."""

    def is_runtime_failure(self, run_id: str) -> bool:
        """Return True if the run failed due to a transient runtime issue."""

    def recover(self, run_id: str) -> None:
        """Trigger runtime recovery for a run."""


class GasCityAdapter(OrchestrationPort):
    """Adapter that maps Doom orchestration calls to Gas City.

    A durable JSON store persists the mapping between Doom run ids and Gas
    City run ids. After a restart, a new adapter instance can load the store
    and continue observing/recovering the same runs. Re-enqueueing with the
    same Doom run id is a no-op and reuses the existing mapping.
    """

    def __init__(self, client: GasCityClient, store_path: str | Path) -> None:
        self._client = client
        self._store_path = Path(store_path)
        self._mapping: dict[str, str] = {}
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_store()

    def _load_store(self) -> None:
        if self._store_path.exists():
            data = json.loads(self._store_path.read_text())
            if isinstance(data, dict):
                self._mapping = {str(k): str(v) for k, v in data.items()}
            else:
                self._mapping = {}

    def _persist(self) -> None:
        self._store_path.write_text(json.dumps(self._mapping, indent=2))

    def enqueue(
        self,
        task: Any,
        dependencies: tuple[str, ...] = (),
        run_id: str | None = None,
    ) -> str:
        """Enqueue a task and return a Doom run id.

        If ``run_id`` is provided and already present in the durable mapping,
        the existing Doom run id is returned without contacting Gas City,
        making resubmission after a restart idempotent.
        """
        if not dependencies:
            task_dependencies = getattr(task, "dependencies", ())
            if isinstance(task_dependencies, (tuple, list)):
                dependencies = tuple(str(d) for d in task_dependencies)
        if run_id is None:
            doom_run_id = f"doom_{uuid.uuid4().hex[:8]}"
        else:
            doom_run_id = run_id
            if doom_run_id in self._mapping:
                return doom_run_id
        gas_run_id = self._client.enqueue(task, dependencies)
        self._mapping[doom_run_id] = gas_run_id
        self._persist()
        return doom_run_id

    def observe(self, run_id: str) -> RunState:
        """Observe the current state of a run, mapping to Gas City."""
        gas_run_id = self._mapping[run_id]
        gas_state = self._client.observe(gas_run_id)
        if isinstance(gas_state, dict):
            status = gas_state.get("status", "")
            dependencies = gas_state.get("dependencies", ())
        else:
            status = getattr(gas_state, "status", "")
            dependencies = getattr(gas_state, "dependencies", ())
        return RunState(run_id=run_id, status=status, dependencies=tuple(dependencies))

    def is_runtime_failure(self, run_id: str) -> bool:
        """Delegate runtime-failure detection to Gas City."""
        gas_run_id = self._mapping[run_id]
        return self._client.is_runtime_failure(gas_run_id)

    def recover(self, run_id: str) -> None:
        """Trigger runtime recovery for a run."""
        gas_run_id = self._mapping[run_id]
        self._client.recover(gas_run_id)
