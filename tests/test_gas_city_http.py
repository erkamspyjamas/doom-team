"""Tests for the HTTP Gas City client and adapter idempotency.

Covers deterministic retry/timeout/error-normalization logic with fakes and
adapter restart-idempotency through the durable run-id mapping. The HTTP
client builds real urllib requests; the transport layer is injected so the
retry/timeout/error behavior is fully deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

from doom_team.gas_city_adapter import GasCityAdapter
from doom_team.gas_city_http import (
    GasCityConfig,
    GasCityPermanentError,
    GasCityTransientError,
    HttpGasCityClient,
)


class FakeResponse:
    """Minimal context-manager response compatible with urllib.urlopen."""

    def __init__(self, status: int, body: str) -> None:
        self._status = status
        self._body = body.encode()

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeURLopener:
    """Returns a queue of responses or raises exceptions in order."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, req: urllib_request.Request, timeout: float | None = None) -> Any:
        self.calls += 1
        if not self.responses:
            raise AssertionError("No responses left for fake URL opener")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeTask:
    def __init__(self, dependencies: tuple[str, ...] = ()) -> None:
        self.dependencies = dependencies


def _config(url: str = "http://gas.test") -> GasCityConfig:
    return GasCityConfig(url=url, timeout=0.1, max_retries=3, backoff_base=0.0)


# --- Deterministic unit tests ---


def test_successful_enqueue() -> None:
    opener = FakeURLopener([FakeResponse(200, '{"run_id":"gas_1"}')])
    client = HttpGasCityClient(_config(), urlopen=opener)
    assert client.enqueue(FakeTask()) == "gas_1"
    assert opener.calls == 1


def test_transient_failure_retries_then_succeeds() -> None:
    opener = FakeURLopener(
        [
            FakeResponse(503, "unavailable"),
            FakeResponse(200, '{"run_id":"gas_2"}'),
        ]
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    assert client.enqueue(FakeTask()) == "gas_2"
    assert opener.calls == 2


def test_transient_failure_exhausts_retries() -> None:
    opener = FakeURLopener(
        [FakeResponse(503, "unavailable")] * 4
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    with pytest.raises(GasCityTransientError):
        client.enqueue(FakeTask())
    assert opener.calls == 4  # initial + 3 retries


def test_permanent_failure_does_not_retry() -> None:
    opener = FakeURLopener([FakeResponse(400, "bad request")])
    client = HttpGasCityClient(_config(), urlopen=opener)
    with pytest.raises(GasCityPermanentError):
        client.enqueue(FakeTask())
    assert opener.calls == 1


def test_network_timeout_is_transient_and_retries() -> None:
    opener = FakeURLopener(
        [
            urllib_error.URLError("timed out"),
            FakeResponse(200, '{"run_id":"gas_3"}'),
        ]
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    assert client.enqueue(FakeTask()) == "gas_3"
    assert opener.calls == 2


def test_malformed_json_is_permanent() -> None:
    opener = FakeURLopener([FakeResponse(200, "not-json")])
    client = HttpGasCityClient(_config(), urlopen=opener)
    with pytest.raises(GasCityPermanentError):
        client.enqueue(FakeTask())
    assert opener.calls == 1


def test_missing_run_id_is_permanent() -> None:
    opener = FakeURLopener([FakeResponse(200, "{}")])
    client = HttpGasCityClient(_config(), urlopen=opener)
    with pytest.raises(GasCityPermanentError):
        client.enqueue(FakeTask())


def test_observe_returns_dict() -> None:
    opener = FakeURLopener(
        [FakeResponse(200, '{"status":"pending","dependencies":[]}')]
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    assert client.observe("gas_1") == {"status": "pending", "dependencies": []}


def test_is_runtime_failure() -> None:
    opener = FakeURLopener(
        [FakeResponse(200, '{"is_runtime_failure":true}')]
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    assert client.is_runtime_failure("gas_1") is True


def test_recover_sends_request() -> None:
    opener = FakeURLopener([FakeResponse(200, '{"ok":true}')])
    client = HttpGasCityClient(_config(), urlopen=opener)
    client.recover("gas_1")
    assert opener.calls == 1


# --- Adapter-level tests (deterministic transport) ---


def test_adapter_enqueue_observe_retry(tmp_path: Path) -> None:
    """The adapter drives enqueue/observe with a transient 503 retried once."""
    opener = FakeURLopener(
        [
            FakeResponse(200, '{"run_id":"gas_1"}'),
            FakeResponse(503, "unavailable"),
            FakeResponse(200, '{"status":"pending","dependencies":[]}'),
            FakeResponse(200, '{"is_runtime_failure":false}'),
        ]
    )
    client = HttpGasCityClient(_config(), urlopen=opener)
    adapter = GasCityAdapter(client, tmp_path / "store.json")

    run_id = adapter.enqueue(FakeTask())
    assert run_id.startswith("doom_")

    state = adapter.observe(run_id)
    assert state.status == "pending"
    assert opener.calls == 3  # enqueue + transient observe + retried observe

    assert adapter.is_runtime_failure(run_id) is False
    assert opener.calls == 4


def test_adapter_restart_idempotent_enqueue(tmp_path: Path) -> None:
    """Re-submitting the same Doom run id after a restart reuses the mapping."""
    opener = FakeURLopener([FakeResponse(200, '{"run_id":"gas_1"}')])
    client = HttpGasCityClient(_config(), urlopen=opener)
    store_path = tmp_path / "store.json"
    adapter1 = GasCityAdapter(client, store_path)

    run_id = adapter1.enqueue(FakeTask())
    gas_id_before = adapter1._mapping[run_id]
    calls_before = opener.calls

    # Simulate a restart with a fresh adapter and the same run id.
    adapter2 = GasCityAdapter(client, store_path)
    returned = adapter2.enqueue(FakeTask(), run_id=run_id)
    assert returned == run_id
    assert adapter2._mapping[run_id] == gas_id_before
    # No new remote run was created.
    assert opener.calls == calls_before
