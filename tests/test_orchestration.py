"""Tests for the Gas City orchestration adapter."""

from pathlib import Path
from typing import Any

from doom_team.gas_city_adapter import GasCityAdapter
from doom_team.ports import OrchestrationPort


class FakeGasCityClient:
    """A simple in-memory Gas City client for tests."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        run_id = f"gas_{self._next_id}"
        self._next_id += 1
        self.runs[run_id] = {
            "task": task,
            "status": "pending",
            "dependencies": dependencies,
        }
        return run_id

    def observe(self, run_id: str) -> Any:
        return self.runs[run_id]

    def is_runtime_failure(self, run_id: str) -> bool:
        return self.runs[run_id]["status"] == "failed_runtime"

    def recover(self, run_id: str) -> None:
        if self.runs[run_id]["status"] == "failed_runtime":
            self.runs[run_id]["status"] = "recovered"


class FakeTask:
    def __init__(self, dependencies: tuple[str, ...] = ()) -> None:
        self.dependencies = dependencies


def test_adapter_implements_orchestration_port(tmp_path: Path) -> None:
    client = FakeGasCityClient()
    adapter = GasCityAdapter(client, tmp_path / "store.json")
    assert isinstance(adapter, OrchestrationPort)


def test_enqueue_and_observe_dependencies(tmp_path: Path) -> None:
    client = FakeGasCityClient()
    adapter = GasCityAdapter(client, tmp_path / "store.json")
    run_id = adapter.enqueue(FakeTask(dependencies=("dep1", "dep2")))
    observed = adapter.observe(run_id)
    assert observed.run_id == run_id
    assert observed.status == "pending"
    assert observed.dependencies == ("dep1", "dep2")


def test_restart_durability(tmp_path: Path) -> None:
    store_path = tmp_path / "store.json"
    client = FakeGasCityClient()
    adapter1 = GasCityAdapter(client, store_path)
    run_id = adapter1.enqueue(FakeTask())

    # Simulate a restart by creating a new adapter with the same store and client.
    adapter2 = GasCityAdapter(client, store_path)
    observed = adapter2.observe(run_id)
    assert observed.run_id == run_id
    assert observed.status == "pending"


def test_done_does_not_imply_acceptance(tmp_path: Path) -> None:
    client = FakeGasCityClient()
    adapter = GasCityAdapter(client, tmp_path / "store.json")
    run_id = adapter.enqueue(FakeTask())
    # Mark the run as completed in Gas City.
    gas_run_id = adapter._mapping[run_id]
    client.runs[gas_run_id]["status"] = "completed"
    observed = adapter.observe(run_id)
    assert observed.status == "completed"
    # There is no acceptance state in the orchestration result.
    assert not hasattr(observed, "acceptance")


def test_runtime_failure_and_recovery(tmp_path: Path) -> None:
    client = FakeGasCityClient()
    adapter = GasCityAdapter(client, tmp_path / "store.json")
    run_id = adapter.enqueue(FakeTask())
    gas_run_id = adapter._mapping[run_id]
    client.runs[gas_run_id]["status"] = "failed_runtime"
    assert adapter.is_runtime_failure(run_id) is True
    adapter.recover(run_id)
    assert client.runs[gas_run_id]["status"] == "recovered"
    assert adapter.is_runtime_failure(run_id) is False
