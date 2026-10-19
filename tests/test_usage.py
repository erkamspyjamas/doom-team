'''Tests for the read-only provider usage aggregation module.'''

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from doom_team.usage import (
    EventLogError,
    ModelUsage,
    UsageReport,
    aggregate_event_log,
    aggregate_events,
    aggregate_lines,
    aggregate_usage,
)


def write_log(tmp_path: Path, lines: list[str]) -> Path:
    events_dir = tmp_path / 'events'
    events_dir.mkdir(parents=True)
    path = events_dir / 'events.jsonl'
    path.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    return path


def usage_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        'kind': 'PROVIDER_USAGE',
        'provider': 'alpha',
        'model': 'a',
        'prompt_tokens': 1,
        'completion_tokens': 2,
    }
    event.update(overrides)
    return event


def test_aggregates_totals_and_sorts_by_provider_then_model(tmp_path: Path) -> None:
    events = [
        {'kind': 'LEGACY_EVENT', 'payload': {'ignored': True}},
        usage_event(
            provider='zeta',
            model='m2',
            prompt_tokens=3,
            completion_tokens=4,
            reasoning_tokens=1,
            cost_usd=0.5,
            finish_reason='length',
        ),
        {'kind': 'PROVIDER_ERROR', 'details': {'error': 'boom'}},
        usage_event(provider='alpha', model='b', prompt_tokens=1, completion_tokens=2),
        {'kind': 'PROVIDER_FALLBACK', 'details': {'from_route': 'x', 'to_route': 'y'}},
        usage_event(
            provider='alpha',
            model='a',
            prompt_tokens=5,
            completion_tokens=6,
            reasoning_tokens=0,
            cost_usd=0.0,
            finish_reason='length',
        ),
    ]
    write_log(tmp_path, [''] + [json.dumps(event) for event in events] + ['   '])

    report = aggregate_event_log(tmp_path)

    assert report.successful_calls == 3
    assert report.prompt_tokens == 9
    assert report.completion_tokens == 12
    assert report.reasoning_tokens == 1
    assert report.known_cost_usd == pytest.approx(0.5)
    assert report.calls_without_cost == 1
    assert report.calls_without_reasoning_count == 1
    assert report.provider_errors == 1
    assert report.fallback_events == 1
    assert report.length_finishes == 2
    assert [(row.provider, row.model) for row in report.by_model] == [
        ('alpha', 'a'),
        ('alpha', 'b'),
        ('zeta', 'm2'),
    ]

    alpha_a, alpha_b, zeta_m2 = report.by_model
    assert alpha_a == ModelUsage(
        provider='alpha',
        model='a',
        successful_calls=1,
        prompt_tokens=5,
        completion_tokens=6,
        reasoning_tokens=0,
        known_cost_usd=0.0,
        calls_without_cost=0,
        calls_without_reasoning_count=0,
        length_finishes=1,
    )
    assert alpha_b.reasoning_tokens is None
    assert alpha_b.known_cost_usd is None
    assert alpha_b.calls_without_cost == 1
    assert alpha_b.calls_without_reasoning_count == 1
    assert zeta_m2.reasoning_tokens == 1
    assert zeta_m2.known_cost_usd == pytest.approx(0.5)


def test_provider_and_model_fall_back_to_event_fields(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        [
            json.dumps(
                {
                    'kind': 'PROVIDER_USAGE',
                    'provider': 'fallback-provider',
                    'model': 'fallback-model',
                    'prompt_tokens': 1,
                    'completion_tokens': 2,
                }
            )
        ],
    )
    report = aggregate_event_log(tmp_path)
    assert len(report.by_model) == 1
    row = report.by_model[0]
    assert row.provider == 'fallback-provider'
    assert row.model == 'fallback-model'
    assert row.successful_calls == 1


def test_details_and_nested_usage_are_supported(tmp_path: Path) -> None:
    event = {
        'kind': 'PROVIDER_USAGE',
        'provider': 'p',
        'model': 'm',
        'details': {
            'usage': {
                'prompt_tokens': 2,
                'completion_tokens': 3,
                'reasoning_tokens': 4,
                'cost_usd': 0.25,
            },
            'finish_reason': 'length',
        },
    }
    write_log(tmp_path, [json.dumps(event)])
    report = aggregate_event_log(tmp_path)
    row = report.by_model[0]
    assert (row.provider, row.model) == ('p', 'm')
    assert row.prompt_tokens == 2
    assert row.completion_tokens == 3
    assert row.reasoning_tokens == 4
    assert row.known_cost_usd == pytest.approx(0.25)
    assert row.length_finishes == 1


def test_unknown_cost_and_reasoning_are_null_not_zero(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        [
            json.dumps(usage_event(provider='p', model='m')),
            json.dumps(
                usage_event(
                    provider='p',
                    model='m',
                    prompt_tokens=3,
                    completion_tokens=4,
                )
            ),
        ],
    )
    report = aggregate_event_log(tmp_path)
    assert report.known_cost_usd is None
    assert report.reasoning_tokens is None
    assert report.calls_without_cost == 2
    assert report.calls_without_reasoning_count == 2
    assert report.by_model[0].known_cost_usd is None
    assert report.by_model[0].reasoning_tokens is None
    assert report.by_model[0].successful_calls == 2
    assert report.by_model[0].prompt_tokens == 4
    assert report.by_model[0].completion_tokens == 6


def test_explicit_zero_cost_and_reasoning_are_known(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        [json.dumps(usage_event(cost_usd=0, reasoning_tokens=0))],
    )
    report = aggregate_event_log(tmp_path)
    assert report.known_cost_usd == 0.0
    assert report.reasoning_tokens == 0
    assert report.calls_without_cost == 0
    assert report.calls_without_reasoning_count == 0
    assert report.by_model[0].known_cost_usd == 0.0
    assert report.by_model[0].reasoning_tokens == 0


def test_only_blank_and_legacy_lines_yields_empty_aggregate(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        [
            '',
            '   ',
            json.dumps({'kind': 'OLD_EVENT', 'details': {'x': 1}}),
            json.dumps({'kind': 'PROVIDER_USAGE_LEGACY'}),
        ],
    )
    assert aggregate_event_log(tmp_path) == UsageReport()


def test_existing_state_without_event_log_is_empty(tmp_path: Path) -> None:
    assert aggregate_event_log(tmp_path) == UsageReport()


def test_missing_event_log_creates_nothing(tmp_path: Path) -> None:
    assert aggregate_event_log(tmp_path) == UsageReport()
    assert list(tmp_path.iterdir()) == []


def test_missing_or_non_directory_state_raises(tmp_path: Path) -> None:
    with pytest.raises(EventLogError):
        aggregate_event_log(tmp_path / 'missing')
    state_file = tmp_path / 'state-file'
    state_file.write_text('not a directory', encoding='utf-8')
    with pytest.raises(EventLogError):
        aggregate_event_log(state_file)


def test_malformed_json_fails_without_echoing_contents(tmp_path: Path) -> None:
    secret = 'RAW-SECRET-CONTENT'
    bad = json.dumps(
        {'kind': 'PROVIDER_USAGE', 'prompt_tokens': 1, 'secret': secret}
    )
    write_log(tmp_path, [bad[:-1]])
    with pytest.raises(EventLogError) as excinfo:
        aggregate_event_log(tmp_path)
    message = str(excinfo.value)
    assert 'line 1' in message
    assert secret not in message
    assert '{' not in message


def test_non_mapping_details_fails_closed(tmp_path: Path) -> None:
    write_log(tmp_path, [json.dumps(usage_event(details=['not', 'an', 'object']))])
    with pytest.raises(EventLogError, match='details must be a JSON object'):
        aggregate_event_log(tmp_path)


def test_missing_prompt_tokens_fails_closed(tmp_path: Path) -> None:
    event = usage_event()
    event.pop('prompt_tokens')
    write_log(tmp_path, [json.dumps(event)])
    with pytest.raises(EventLogError, match='prompt_tokens'):
        aggregate_event_log(tmp_path)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('prompt_tokens', -1),
        ('prompt_tokens', True),
        ('prompt_tokens', 1.5),
        ('prompt_tokens', '1'),
        ('completion_tokens', -1),
        ('completion_tokens', False),
        ('completion_tokens', 2.5),
        ('reasoning_tokens', -1),
        ('reasoning_tokens', True),
        ('reasoning_tokens', 1.5),
        ('cost_usd', -0.1),
        ('cost_usd', True),
        ('cost_usd', '0.1'),
        ('cost_usd', math.nan),
        ('cost_usd', math.inf),
        ('cost', -1),
    ],
)
def test_invalid_usage_counters_and_costs_fail_closed(
    tmp_path: Path, field: str, value: Any
) -> None:
    write_log(tmp_path, [json.dumps(usage_event(**{field: value}))])
    with pytest.raises(EventLogError) as excinfo:
        aggregate_event_log(tmp_path)
    message = str(excinfo.value)
    assert 'line 1' in message
    assert field in message
    assert '{' not in message


def test_provider_errors_and_fallbacks_do_not_affect_usage_totals(
    tmp_path: Path,
) -> None:
    write_log(
        tmp_path,
        [
            json.dumps({'kind': 'PROVIDER_ERROR', 'details': {'prompt_tokens': 999}}),
            json.dumps(
                {'kind': 'PROVIDER_FALLBACK', 'details': {'completion_tokens': 999}}
            ),
            json.dumps(usage_event(prompt_tokens=1, completion_tokens=2)),
        ],
    )
    report = aggregate_event_log(tmp_path)
    assert report.successful_calls == 1
    assert report.prompt_tokens == 1
    assert report.completion_tokens == 2
    assert report.provider_errors == 1
    assert report.fallback_events == 1


def test_by_model_groups_duplicate_provider_model(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        [
            json.dumps(
                usage_event(
                    provider='p',
                    model='m',
                    prompt_tokens=1,
                    completion_tokens=2,
                    cost_usd=0.1,
                    reasoning_tokens=3,
                )
            ),
            json.dumps(
                usage_event(
                    provider='p',
                    model='m',
                    prompt_tokens=4,
                    completion_tokens=5,
                    cost_usd=0.2,
                    reasoning_tokens=6,
                )
            ),
        ],
    )
    report = aggregate_event_log(tmp_path)
    assert len(report.by_model) == 1
    row = report.by_model[0]
    assert row.successful_calls == 2
    assert row.prompt_tokens == 5
    assert row.completion_tokens == 7
    assert row.reasoning_tokens == 9
    assert row.known_cost_usd == pytest.approx(0.3)


def test_as_dict_has_strict_schema_and_no_extra_fields(tmp_path: Path) -> None:
    write_log(tmp_path, [json.dumps(usage_event())])
    data = aggregate_event_log(tmp_path).as_dict()
    assert data['schema_version'] == 1
    assert set(data) == {
        'schema_version',
        'successful_calls',
        'prompt_tokens',
        'completion_tokens',
        'reasoning_tokens',
        'known_cost_usd',
        'calls_without_cost',
        'calls_without_reasoning_count',
        'provider_errors',
        'fallback_events',
        'length_finishes',
        'by_model',
    }
    assert isinstance(data['by_model'], list)
    assert set(data['by_model'][0]) == {
        'provider',
        'model',
        'successful_calls',
        'prompt_tokens',
        'completion_tokens',
        'reasoning_tokens',
        'known_cost_usd',
        'calls_without_cost',
        'calls_without_reasoning_count',
        'length_finishes',
    }


def test_aggregate_events_accepts_parsed_mappings() -> None:
    report = aggregate_events([usage_event(prompt_tokens=2, completion_tokens=3)])
    assert report.successful_calls == 1
    assert report.prompt_tokens == 2
    assert report.completion_tokens == 3


def test_aggregate_lines_ignores_blank_lines() -> None:
    report = aggregate_lines(['', json.dumps(usage_event()), '   '])
    assert report.successful_calls == 1
    assert len(report.by_model) == 1


def test_aggregation_is_read_only(tmp_path: Path) -> None:
    path = write_log(tmp_path, [json.dumps(usage_event())])
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    report = aggregate_usage(tmp_path)
    assert report.successful_calls == 1
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert sorted(item.name for item in tmp_path.iterdir()) == ['events']
    assert sorted(item.name for item in (tmp_path / 'events').iterdir()) == [
        'events.jsonl'
    ]


def test_event_must_be_json_object(tmp_path: Path) -> None:
    write_log(tmp_path, ['[]'])
    with pytest.raises(EventLogError, match='event must be a JSON object'):
        aggregate_event_log(tmp_path)


def test_invalid_utf8_fails_closed(tmp_path: Path) -> None:
    events_dir = tmp_path / 'events'
    events_dir.mkdir()
    (events_dir / 'events.jsonl').write_bytes(bytes([0xFF, 0x0A]))
    with pytest.raises(EventLogError, match='UTF-8'):
        aggregate_event_log(tmp_path)
