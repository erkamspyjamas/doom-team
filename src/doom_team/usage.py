'''Read-only aggregation of provider usage events.

The operator-facing usage command needs a reusable summary of provider usage
without touching a store or the network. This module reads
<state>/events/events.jsonl directly and aggregates only the event kinds it
understands. Unknown cost/reasoning values stay explicitly unknown (None)
rather than being coerced to zero.
'''

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final[int] = 1
USAGE_KIND: Final[str] = 'PROVIDER_USAGE'
ERROR_KIND: Final[str] = 'PROVIDER_ERROR'
FALLBACK_KIND: Final[str] = 'PROVIDER_FALLBACK'
UNKNOWN: Final[str] = 'unknown'


class EventLogError(Exception):
    '''Raised when the event log cannot be aggregated safely.'''


UsageAggregationError = EventLogError
AggregationError = EventLogError
UsageError = EventLogError


@dataclass(frozen=True)
class ModelUsage:
    '''Aggregated usage for one actual (provider, model) pair.'''

    provider: str
    model: str
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    known_cost_usd: float | None = None
    calls_without_cost: int = 0
    calls_without_reasoning_count: int = 0
    length_finishes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            'provider': self.provider,
            'model': self.model,
            'successful_calls': self.successful_calls,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'known_cost_usd': self.known_cost_usd,
            'calls_without_cost': self.calls_without_cost,
            'calls_without_reasoning_count': self.calls_without_reasoning_count,
            'length_finishes': self.length_finishes,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class UsageReport:
    '''Total and per-model provider usage for one state directory.'''

    schema_version: int = SCHEMA_VERSION
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    known_cost_usd: float | None = None
    calls_without_cost: int = 0
    calls_without_reasoning_count: int = 0
    provider_errors: int = 0
    fallback_events: int = 0
    length_finishes: int = 0
    by_model: list[ModelUsage] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'successful_calls': self.successful_calls,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'known_cost_usd': self.known_cost_usd,
            'calls_without_cost': self.calls_without_cost,
            'calls_without_reasoning_count': self.calls_without_reasoning_count,
            'provider_errors': self.provider_errors,
            'fallback_events': self.fallback_events,
            'length_finishes': self.length_finishes,
            'by_model': [row.as_dict() for row in self.by_model],
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


UsageAggregate = UsageReport
UsageSummary = UsageReport
ModelUsageRow = ModelUsage
UsageModel = ModelUsage


@dataclass
class _Totals:
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_seen: bool = False
    known_cost_usd: float = 0.0
    cost_seen: bool = False
    calls_without_cost: int = 0
    calls_without_reasoning_count: int = 0
    length_finishes: int = 0

    def add_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int | None,
        known_cost_usd: float | None,
        length_finish: bool,
    ) -> None:
        self.successful_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if reasoning_tokens is None:
            self.calls_without_reasoning_count += 1
        else:
            self.reasoning_tokens += reasoning_tokens
            self.reasoning_seen = True
        if known_cost_usd is None:
            self.calls_without_cost += 1
        else:
            self.known_cost_usd += known_cost_usd
            self.cost_seen = True
        if length_finish:
            self.length_finishes += 1

    @property
    def reasoning_total(self) -> int | None:
        return self.reasoning_tokens if self.reasoning_seen else None

    @property
    def cost_total(self) -> float | None:
        return self.known_cost_usd if self.cost_seen else None

    def as_model_usage(self, provider: str, model: str) -> ModelUsage:
        return ModelUsage(
            provider=provider,
            model=model,
            successful_calls=self.successful_calls,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_total,
            known_cost_usd=self.cost_total,
            calls_without_cost=self.calls_without_cost,
            calls_without_reasoning_count=self.calls_without_reasoning_count,
            length_finishes=self.length_finishes,
        )


_MISSING: Final[object] = object()


def _nonnegative_int(value: Any, field_name: str, lineno: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EventLogError(
            f'line {lineno}: {field_name} must be a nonnegative integer'
        )
    return value


def _nonnegative_cost(value: Any, field_name: str, lineno: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise EventLogError(
            f'line {lineno}: {field_name} must be a finite nonnegative number'
        )
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EventLogError(
            f'line {lineno}: {field_name} must be a finite nonnegative number'
        )
    return number


def _event_sources(
    event: Mapping[str, Any], lineno: int
) -> tuple[Mapping[str, Any], ...]:
    sources: list[Mapping[str, Any]] = []
    details = event.get('details')
    if details is not None:
        if not isinstance(details, Mapping):
            raise EventLogError(f'line {lineno}: details must be a JSON object')
        sources.append(details)
        nested = details.get('usage')
        if isinstance(nested, Mapping):
            sources.append(nested)
    top_level_usage = event.get('usage')
    if isinstance(top_level_usage, Mapping):
        sources.append(top_level_usage)
    sources.append(event)
    return tuple(sources)


def _lookup(sources: tuple[Mapping[str, Any], ...], name: str) -> Any:
    for source in sources:
        if name in source:
            return source[name]
    return _MISSING


def _text_field(
    sources: tuple[Mapping[str, Any], ...],
    names: tuple[str, ...],
    default: str,
) -> str:
    for name in names:
        for source in sources:
            if name not in source:
                continue
            value = source[name]
            if value is None:
                continue
            if isinstance(value, str):
                if value:
                    return value
                continue
            return str(value)
    return default


def _required_counter(
    sources: tuple[Mapping[str, Any], ...], name: str, lineno: int
) -> int:
    value = _lookup(sources, name)
    if value is _MISSING:
        raise EventLogError(
            f'line {lineno}: {name} must be a nonnegative integer'
        )
    return _nonnegative_int(value, name, lineno)


def _optional_reasoning(
    sources: tuple[Mapping[str, Any], ...], lineno: int
) -> int | None:
    value = _lookup(sources, 'reasoning_tokens')
    if value is _MISSING or value is None:
        return None
    return _nonnegative_int(value, 'reasoning_tokens', lineno)


def _optional_cost(
    sources: tuple[Mapping[str, Any], ...], lineno: int
) -> float | None:
    name = 'cost_usd'
    value = _lookup(sources, name)
    if value is _MISSING:
        name = 'cost'
        value = _lookup(sources, name)
    if value is _MISSING or value is None:
        return None
    return _nonnegative_cost(value, name, lineno)


def _length_finish(sources: tuple[Mapping[str, Any], ...]) -> bool:
    for name in ('finish_reason', 'stop_reason'):
        value = _lookup(sources, name)
        if isinstance(value, str) and value.strip().lower() == 'length':
            return True
    return False


def _consume_usage(
    event: Mapping[str, Any],
    lineno: int,
    total: _Totals,
    by_model: dict[tuple[str, str], _Totals],
) -> None:
    sources = _event_sources(event, lineno)
    provider = _text_field(
        sources, ('provider', 'provider_name', 'provider_id'), UNKNOWN
    )
    model = _text_field(sources, ('model', 'model_name', 'model_id'), UNKNOWN)
    prompt_tokens = _required_counter(sources, 'prompt_tokens', lineno)
    completion_tokens = _required_counter(sources, 'completion_tokens', lineno)
    reasoning_tokens = _optional_reasoning(sources, lineno)
    known_cost_usd = _optional_cost(sources, lineno)
    length_finish = _length_finish(sources)

    total.add_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        known_cost_usd=known_cost_usd,
        length_finish=length_finish,
    )
    key = (provider, model)
    model_totals = by_model.get(key)
    if model_totals is None:
        model_totals = _Totals()
        by_model[key] = model_totals
    model_totals.add_usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        known_cost_usd=known_cost_usd,
        length_finish=length_finish,
    )


def _event_pairs(
    events: Iterable[Mapping[str, Any]],
) -> Iterable[tuple[int, Mapping[str, Any]]]:
    for lineno, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            raise EventLogError(f'line {lineno}: event must be a JSON object')
        yield lineno, event


def _aggregate(
    events: Iterable[tuple[int, Mapping[str, Any]]],
) -> UsageReport:
    total = _Totals()
    by_model: dict[tuple[str, str], _Totals] = {}
    provider_errors = 0
    fallback_events = 0
    for lineno, event in events:
        kind_value = event.get('kind')
        kind = kind_value.strip().upper() if isinstance(kind_value, str) else ''
        if kind == USAGE_KIND:
            _consume_usage(event, lineno, total, by_model)
        elif kind == ERROR_KIND:
            provider_errors += 1
        elif kind == FALLBACK_KIND:
            fallback_events += 1

    rows = [
        row.as_model_usage(provider, model)
        for (provider, model), row in sorted(
            by_model.items(), key=lambda item: item[0]
        )
    ]
    return UsageReport(
        successful_calls=total.successful_calls,
        prompt_tokens=total.prompt_tokens,
        completion_tokens=total.completion_tokens,
        reasoning_tokens=total.reasoning_total,
        known_cost_usd=total.cost_total,
        calls_without_cost=total.calls_without_cost,
        calls_without_reasoning_count=total.calls_without_reasoning_count,
        provider_errors=provider_errors,
        fallback_events=fallback_events,
        length_finishes=total.length_finishes,
        by_model=rows,
    )


def aggregate_events(events: Iterable[Mapping[str, Any]]) -> UsageReport:
    '''Aggregate already-parsed event mappings (line numbers start at 1).'''
    return _aggregate(_event_pairs(events))


def aggregate_lines(lines: Iterable[str]) -> UsageReport:
    '''Aggregate JSONL text lines, ignoring blank lines and unknown kinds.'''

    def parsed() -> Iterable[tuple[int, Mapping[str, Any]]]:
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise EventLogError(f'line {lineno}: invalid JSON') from None
            if not isinstance(event, Mapping):
                raise EventLogError(f'line {lineno}: event must be a JSON object')
            yield lineno, event

    return _aggregate(parsed())


def aggregate_event_log(state_dir: str | Path) -> UsageReport:
    '''Read <state>/events/events.jsonl directly and aggregate it.

    No store is constructed and no file or directory is created. An existing
    state directory without an event log is a successful empty report.
    '''
    root = Path(state_dir)
    try:
        if not root.exists() or not root.is_dir():
            raise EventLogError('state directory is missing or not a directory')
    except OSError:
        raise EventLogError('state directory is missing or not a directory') from None

    path = root / 'events' / 'events.jsonl'
    try:
        if not path.exists():
            return UsageReport()
        if not path.is_file():
            raise EventLogError('event log is not a regular file')
        with path.open('r', encoding='utf-8') as handle:
            try:
                return aggregate_lines(handle)
            except UnicodeDecodeError:
                raise EventLogError('event log is not valid UTF-8') from None
    except OSError:
        raise EventLogError('event log cannot be read') from None


def aggregate_usage(state_dir: str | Path) -> UsageReport:
    '''Alias for aggregate_event_log used by the operator command.'''
    return aggregate_event_log(state_dir)


def read_usage(state_dir: str | Path) -> UsageReport:
    '''Read-only alias for aggregate_event_log.'''
    return aggregate_event_log(state_dir)


__all__ = [
    'AggregationError',
    'ERROR_KIND',
    'EventLogError',
    'FALLBACK_KIND',
    'ModelUsage',
    'ModelUsageRow',
    'SCHEMA_VERSION',
    'UNKNOWN',
    'USAGE_KIND',
    'UsageAggregate',
    'UsageAggregationError',
    'UsageError',
    'UsageModel',
    'UsageReport',
    'UsageSummary',
    'aggregate_event_log',
    'aggregate_events',
    'aggregate_lines',
    'aggregate_usage',
    'read_usage',
]
