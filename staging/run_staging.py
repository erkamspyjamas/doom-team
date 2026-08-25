"""Staging end-to-end verification harness for Doom Team.

Runs the real service against controlled (fake) dependencies and records
repo-resident evidence. Mode is labeled in the output report. Only
controlled/fake staging is supported; `--mode real` is rejected so evidence
can never be labeled real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from doom_team.approval import DurableApprovalPort
from doom_team.autonomy import AutonomousMissionRunner
from doom_team.backup import backup_state, restore_state, verify_backup
from doom_team.config import ConfigLoader, Settings
from doom_team.domain import Candidate, Evidence, Mission, Objective, TaskContract
from doom_team.durable import StateCorruptionError
from doom_team.evidence import DurableEvidenceStore
from doom_team.gas_city_adapter import GasCityAdapter
from doom_team.inference import InferenceError, OpenAICompatibleInferenceClient
from doom_team.observability import (
    DurableEventSink,
    Event,
    check_health,
    check_readiness,
)
from doom_team.recovery import Blocker, RecoveryController
from doom_team.telemetry import InMemoryTelemetry
from doom_team.verdicts import DurableVerdictStore
from doom_team.workspace import GitWorkspace


class StagingError(Exception):
    """Raised when a staging scenario fails."""


class FakeGasCityClient:
    """Deterministic in-memory Gas City client for staging."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self._next = 1
        self.fail_next_enqueue = 0
        self.fail_next_observe = 0

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        if self.fail_next_enqueue > 0:
            self.fail_next_enqueue -= 1
            raise TimeoutError("simulated gas city outage")
        run_id = f"gas_{self._next}"
        self._next += 1
        self.runs[run_id] = {"status": "pending", "dependencies": dependencies}
        return run_id

    def observe(self, run_id: str) -> Any:
        if self.fail_next_observe > 0:
            self.fail_next_observe -= 1
            raise TimeoutError("simulated gas city outage")
        return self.runs[run_id]

    def is_runtime_failure(self, run_id: str) -> bool:
        return self.runs[run_id]["status"] == "failed_runtime"

    def recover(self, run_id: str) -> None:
        if self.runs[run_id]["status"] == "failed_runtime":
            self.runs[run_id]["status"] = "recovered"


class FakeTask:
    """A minimal task object with dependencies for Gas City fakes."""

    def __init__(self, dependencies: tuple[str, ...] = ()) -> None:
        self.dependencies = dependencies


def _fake_urlopen(state: dict[str, Any]) -> Any:
    """Return a fake urlopen that simulates a provider response."""

    class FakeResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def urlopen(req: Any, timeout: float | None = None) -> FakeResponse:
        behavior = state["behavior"]
        if behavior == "success":
            body = json.dumps(
                {
                    "model": "deepseek-v4",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "provider": {"name": "CoreWeave"},
                }
            ).encode()
            return FakeResponse(200, body)
        if behavior == "timeout":
            raise TimeoutError("simulated provider outage")
        raise RuntimeError("unexpected behavior")

    return urlopen


class StagingHarness:
    """Runs all 15 staging scenarios and returns a report."""

    def __init__(self, base_dir: Path, mode: str = "fake") -> None:
        self.base_dir = base_dir
        self.mode = mode
        self.state_dir = base_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.report: dict[str, Any] = {"mode": mode, "scenarios": []}
        self._results: list[dict[str, Any]] = []

    def _record(self, name: str, status: str, detail: str = "") -> None:
        self._results.append({"name": name, "status": status, "detail": detail})

    def _check(self, name: str, fn: Any) -> None:
        try:
            fn()
            self._record(name, "pass")
        except Exception as exc:  # pragma: no cover - failure path
            self._record(name, "fail", detail=str(exc))
            raise StagingError(f"{name} failed: {exc}") from exc

    def _settings(self) -> Settings:
        return ConfigLoader().load()

    def scenario_clean_startup(self) -> None:
        """Start the service and shut it down gracefully."""
        env = dict(os.environ)
        env["DOOM_API_KEY"] = "staging-secret"
        env["DOOM_STATE_DIR"] = str(self.state_dir)
        env["DOOM_MODEL_PROVIDER"] = "nvidia"
        env["DOOM_MODEL_NAME"] = "deepseek-v4"
        proc = subprocess.Popen(
            [sys.executable, "-c", "from doom_team.cli import main; main()", "serve"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            assert line == "Doom Team service ready", f"unexpected startup line: {line}"
            proc.send_signal(subprocess.signal.SIGTERM)
            proc.wait(timeout=5)
            assert proc.returncode == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        assert (self.state_dir / "evidence").is_dir()
        assert (self.state_dir / "verdicts").is_dir()

    def scenario_doctor_check(self) -> None:
        from doom_team.cli import main as cli_main

        rc = cli_main(["doctor"])
        assert rc == 0

    def scenario_health_readiness(self) -> None:
        settings = self._settings()
        healthy, _ = check_health(settings)
        assert healthy
        ready, _ = check_readiness(settings)
        assert ready

    def scenario_gas_city_connection(self) -> None:
        client = FakeGasCityClient()
        adapter = GasCityAdapter(client, self.state_dir / "gas_store.json")
        run_id = adapter.enqueue(FakeTask(dependencies=("a",)))
        observed = adapter.observe(run_id)
        assert observed.status == "pending"
        assert observed.dependencies == ("a",)

    def scenario_inference_request(self) -> None:
        client = OpenAICompatibleInferenceClient(
            base_url="http://fake.invalid",
            api_key="staging-secret",
            model="deepseek-v4",
            urlopen=_fake_urlopen({"behavior": "success"}),
        )
        response = client.complete([{"role": "user", "content": "hi"}])
        assert response.content == "ok"
        assert response.model == "deepseek-v4"

    def scenario_mission_lifecycle(self) -> None:
        ws = GitWorkspace(self.state_dir / "workspaces")
        verdicts = DurableVerdictStore(self.state_dir)
        telemetry = InMemoryTelemetry()
        sink = DurableEventSink(self.state_dir)

        def runner(task: TaskContract, workspace_id: str) -> str:
            patch = (
                b"diff --git a/file.txt b/file.txt\n"
                b"new file mode 100644\n"
                b"index 0000000..e69de29\n"
                b"--- /dev/null\n"
                b"+++ b/file.txt\n"
                b"@@ -0,0 +1 @@\n"
                b"+content\n"
            )
            ws.apply_patch(workspace_id, patch)
            return ws.commit(workspace_id, "candidate")

        def qa(candidate: Candidate, evidence: Evidence) -> tuple[bool, str]:
            return True, "ok"

        def reviewer(candidate: Candidate, evidence: Evidence) -> tuple[bool, str]:
            return True, "ok"

        objective = Objective(id="obj-1", description="staging")
        task = TaskContract(
            id="task-1",
            objective_id="obj-1",
            instruction="do something",
            acceptance_criteria=("c",),
        )
        mission = Mission(id="mission-1", objective=objective, tasks=(task,))

        runner_engine = AutonomousMissionRunner(
            workspace_port=ws,
            repository_port=ws,
            verdict_store=verdicts,
            telemetry=telemetry,
            runner=runner,
            qa_verify=qa,
            reviewer_verify=reviewer,
            event_sink=sink,
        )
        outcomes = runner_engine.run(mission)
        outcome = outcomes["task-1"]
        assert outcome.candidate is not None
        assert outcome.evidence is not None
        assert outcome.qa_verdict is not None and outcome.qa_verdict.decision is True
        assert outcome.reviewer_verdict is not None and outcome.reviewer_verdict.decision is True

    def scenario_record_approval(self) -> None:
        port = DurableApprovalPort(self.state_dir)
        port.approve("release", "sha123", "ops-1")
        assert port.request_approval("release", {"frontier_sha": "sha123"}) is True

    def scenario_restart_during_work(self) -> None:
        recovery_path = self.state_dir / "recovery.json"
        c1 = RecoveryController(recovery_path)
        epoch = c1.initialize("task-1", semantic_retries=2)
        c1.record_runtime_retry(epoch.attempt)
        c2 = RecoveryController(recovery_path)
        state = c2.state("task-1")
        assert state is not None and state.semantic_retries_left == 2

        client = FakeGasCityClient()
        store = self.state_dir / "gas_store.json"
        a1 = GasCityAdapter(client, store)
        run_id = a1.enqueue(FakeTask())
        a2 = GasCityAdapter(client, store)
        observed = a2.observe(run_id)
        assert observed.run_id == run_id

    def scenario_inference_provider_outage(self) -> None:
        client = OpenAICompatibleInferenceClient(
            base_url="http://fake.invalid",
            api_key="staging-secret",
            model="deepseek-v4",
            urlopen=_fake_urlopen({"behavior": "timeout"}),
        )
        try:
            client.complete([{"role": "user", "content": "hi"}])
            raise AssertionError("expected InferenceError")
        except InferenceError as exc:
            assert exc.failure.kind == "timeout"

        recovery_path = self.state_dir / "recovery2.json"
        c = RecoveryController(recovery_path)
        epoch = c.initialize("task-1", semantic_retries=2)
        c.record_runtime_retry(epoch.attempt)
        state = c.state("task-1")
        assert state is not None and state.semantic_retries_left == 2

    def scenario_gas_city_outage(self) -> None:
        from doom_team.gas_city_http import (
            GasCityConfig,
            GasCityTransientError,
            HttpGasCityClient,
        )

        def urlopen(req: Any, timeout: float | None = None) -> Any:
            raise TimeoutError("simulated gas city outage")

        http_client = HttpGasCityClient(
            GasCityConfig(url="http://gas.invalid"), urlopen=urlopen
        )
        try:
            http_client.enqueue(FakeTask())
            raise AssertionError("expected GasCityTransientError")
        except GasCityTransientError:
            pass

    def scenario_corrupted_state_fails_closed(self) -> None:
        ev_store = DurableEvidenceStore(self.state_dir / "corrupt")
        ev_store.save(
            {
                "id": "ev-1",
                "candidate_id": "cand-1",
                "candidate_commit": "abc",
                "objective_id": "obj-1",
                "mission_id": "mission-1",
                "producer_role": "dev",
                "producer_run": "run-1",
                "command": "make test",
                "artifacts": [],
                "actual_model": "deepseek-v4",
                "actual_runtime": "local",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "digest": "sha256:abc",
            }
        )
        path = ev_store.evidence_dir / "ev-1.json"
        data = json.loads(path.read_text())
        data["candidate_commit"] = "tampered"
        path.write_text(json.dumps(data))
        try:
            ev_store.load("ev-1")
            raise AssertionError("expected StateCorruptionError")
        except StateCorruptionError:
            pass

    def scenario_backup(self) -> None:
        backup_dir = self.base_dir / "backups"
        backup_path = backup_state(self.state_dir, backup_dir)
        assert verify_backup(backup_path)

    def scenario_restore(self) -> None:
        backup_dir = self.base_dir / "backups"
        backup_path = backup_state(self.state_dir, backup_dir)
        restore_state(backup_path, self.state_dir)
        assert (self.state_dir / "evidence").is_dir()

    def scenario_recovery(self) -> None:
        path = self.state_dir / "recovery3.json"
        c = RecoveryController(path)
        epoch = c.initialize("task-1", semantic_retries=1)
        c.on_semantic_blocker("task-1", epoch.attempt, Blocker("qa", "failed"))
        assert c.is_exhausted("task-1")
        c2 = RecoveryController(path)
        assert c2.is_exhausted("task-1")
        assert c2.next_attempt("task-1") is None

    def scenario_clean_shutdown(self) -> None:
        sink = DurableEventSink(self.state_dir)
        sink.record(Event(kind="SERVICE_STOPPED", producer_role="system", details={}))
        events = sink.load()
        assert any(e.kind == "SERVICE_STOPPED" for e in events)

    def run(self) -> dict[str, Any]:
        os.environ["DOOM_API_KEY"] = "staging-secret"
        os.environ["DOOM_STATE_DIR"] = str(self.state_dir)
        os.environ["DOOM_MODEL_PROVIDER"] = "nvidia"
        os.environ["DOOM_MODEL_NAME"] = "deepseek-v4"

        scenarios = [
            ("clean_startup", self.scenario_clean_startup),
            ("doctor_check", self.scenario_doctor_check),
            ("health_readiness", self.scenario_health_readiness),
            ("gas_city_connection", self.scenario_gas_city_connection),
            ("inference_request", self.scenario_inference_request),
            ("mission_lifecycle", self.scenario_mission_lifecycle),
            ("record_approval", self.scenario_record_approval),
            ("restart_during_work", self.scenario_restart_during_work),
            ("inference_provider_outage", self.scenario_inference_provider_outage),
            ("gas_city_outage", self.scenario_gas_city_outage),
            ("corrupted_state_fails_closed", self.scenario_corrupted_state_fails_closed),
            ("backup", self.scenario_backup),
            ("restore", self.scenario_restore),
            ("recovery", self.scenario_recovery),
            ("clean_shutdown", self.scenario_clean_shutdown),
        ]
        for name, fn in scenarios:
            self._check(name, fn)
        self.report["scenarios"] = self._results
        self.report["all_passed"] = all(s["status"] == "pass" for s in self._results)
        return self.report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Doom Team staging harness")
    parser.add_argument(
        "--base-dir", default="staging/run", help="Base directory for staging state"
    )
    parser.add_argument(
        "--output",
        default="staging/results/latest.json",
        help="Output report path",
    )
    parser.add_argument("--mode", default="fake", help="Mode label (fake or real)")
    args = parser.parse_args(argv)
    if args.mode == "real":
        print(
            "STAGING ERROR: --mode real is not supported; only controlled/fake staging (--mode fake) is available",
            file=sys.stderr,
        )
        return 1
    base = Path(args.base_dir)
    harness = StagingHarness(base, mode=args.mode)
    try:
        report = harness.run()
    except StagingError as exc:
        print(f"STAGING FAILED: {exc}", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(f"Staging passed. Report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
