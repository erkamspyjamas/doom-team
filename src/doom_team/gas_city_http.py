"""HTTP-backed Gas City client with bounded retries and error normalization.

Implements the GasCityClient protocol against a Gas City control plane HTTP
API. Transient failures (timeout, HTTP 408/429/5xx, network errors) are
retried with exponential backoff up to a bounded retry count; permanent
failures are raised immediately. The actual provider/model/runtime route is
left to the caller's telemetry, and no credentials are ever logged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from doom_team.gas_city_adapter import GasCityClient


class GasCityError(Exception):
    """Base error for Gas City communication failures."""


class GasCityTransientError(GasCityError):
    """A transient failure that may succeed after a retry."""


class GasCityPermanentError(GasCityError):
    """A permanent failure that must not be retried."""


@dataclass(frozen=True)
class GasCityConfig:
    """Connection and retry settings for the HTTP client."""

    url: str
    timeout: float = 10.0
    max_retries: int = 3
    backoff_base: float = 0.5


class HttpGasCityClient(GasCityClient):
    """A Gas City client that talks to a control plane over HTTP."""

    def __init__(
        self,
        config: GasCityConfig,
        *,
        urlopen: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._urlopen = urlopen if urlopen is not None else urllib_request.urlopen
        self._sleep = sleep

    def _base_url(self) -> str:
        return self._config.url.rstrip("/")

    @staticmethod
    def _is_transient_status(status: int | None) -> bool:
        """Return True for HTTP statuses that indicate a transient failure."""
        if status is None:
            return True
        return status >= 500 or status in (408, 429)

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff in seconds for the given zero-based attempt."""
        return float(self._config.backoff_base * (2**attempt))

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request with bounded retries and normalized errors."""
        url = f"{self._base_url()}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib_request.Request(url, data=data, method=method, headers=headers)

        for attempt in range(self._config.max_retries + 1):
            try:
                with self._urlopen(req, timeout=self._config.timeout) as response:
                    status = response.getcode()
                    if status is not None and 200 <= status < 300:
                        raw = response.read().decode("utf-8")
                        if not raw:
                            return {}
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise GasCityPermanentError(
                                f"Invalid JSON from Gas City: {exc}"
                            ) from exc
                        if not isinstance(parsed, dict):
                            raise GasCityPermanentError(
                                "Gas City returned a non-object JSON response"
                            )
                        return parsed
                    body = response.read().decode("utf-8", errors="replace")
                    message = f"Gas City HTTP {status}: {body[:200]}"
                    if self._is_transient_status(status):
                        if attempt < self._config.max_retries:
                            self._sleep(self._backoff(attempt))
                            continue
                        raise GasCityTransientError(message)
                    raise GasCityPermanentError(message)
            except urllib_error.HTTPError as exc:
                status = exc.code
                body = exc.read().decode("utf-8", errors="replace")
                message = f"Gas City HTTP {status}: {body[:200]}"
                if self._is_transient_status(status):
                    if attempt < self._config.max_retries:
                        self._sleep(self._backoff(attempt))
                        continue
                    raise GasCityTransientError(message)
                raise GasCityPermanentError(message)
            except (urllib_error.URLError, TimeoutError, OSError) as exc:
                message = f"Gas City network error: {exc}"
                if attempt < self._config.max_retries:
                    self._sleep(self._backoff(attempt))
                    continue
                raise GasCityTransientError(message) from exc

        # This line is unreachable because the loop always returns or raises,
        # but mypy strict requires it for exhaustiveness.
        raise GasCityTransientError("Gas City request failed after retries")

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        """Enqueue a task and return the Gas City run id."""
        payload = {"dependencies": list(dependencies), "task": repr(task)}
        data = self._request("POST", "/enqueue", payload)
        run_id = data.get("run_id")
        if not run_id:
            raise GasCityPermanentError("Gas City enqueue response missing run_id")
        return str(run_id)

    def observe(self, run_id: str) -> dict[str, Any]:
        """Return the current run state as a dict."""
        return self._request("GET", f"/observe/{run_id}")

    def is_runtime_failure(self, run_id: str) -> bool:
        """Return True if the run failed due to a transient runtime issue."""
        data = self._request("GET", f"/is_runtime_failure/{run_id}")
        return bool(data.get("is_runtime_failure", False))

    def recover(self, run_id: str) -> None:
        """Trigger runtime recovery for a run."""
        self._request("POST", f"/recover/{run_id}")
