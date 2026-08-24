"""Gas City orchestration adapter.

Implements OrchestrationPort without leaking Gas City internals into the
domain. The adapter talks to a GasCityClient protocol and persists mapping
between Doom run ids and Gas City run ids in a durable store so that work
can be recovered after a restart.
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
    and continue observing/recovering the same runs.
    """

    def __init__(self, client: GasCityClient, store_path: str | Path) -> None:
        self._client = client
        self._store_path = Path(store_path)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_store()

    def _load_store(self) -> None:
        if self._store_path.exists():
            self._mapping = json.loads(self._store_path.read_text())
        else:
            self._mapping: dict[str, str] = {}

    def _persist(self) -> None:
        self._store_path.write_text(json.dumps(self._mapping, indent=2))

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        """Enqueue a task and return a Doom run id."""
        doom_run_id = f"doom_{uuid.uuid4().hex[:8]}"
        gas_run_id = self._client.enqueue(task, dependencies)
        self._mapping[doom_run_id] = gas_run_id
        self._persist()
        return doom_run_id

    def observe(self, run_id: str) -> RunState:
        """Observe the current state of a run, mapping to Gas City."""
        gas_run_id = self._mapping[run_id]
        gas_state = self._client.observe(gas_run_id)
        dependencies = getattr(gas_state, "dependencies", ())
        return RunState(run_id=run_id, status=gas_state.status, dependencies=tuple(dependencies))

    def is_runtime_failure(self, run_id: str) -> bool:
        """Delegate runtime-failure detection to Gas City."""
        gas_run_id = self._mapping[run_id]
        return self._client.is_runtime_failure(gas_run_id)

    def recover(self, run_id: str) -> None:
        """Trigger runtime recovery for a run."""
        gas_run_id = self._mapping[run_id]
        self._client.recover(gas_run_id)
