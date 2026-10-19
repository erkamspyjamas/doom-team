"""Native-stack commands are executed, reviewed and pinned; no Python gates forced."""
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from doom_team.cli import _build_mission_runner
from doom_team.config import ConfigLoader, Settings
from doom_team.mission import MissionStore
from doom_team.project_validation import ProjectValidationError, load_manifest, run_phase
from doom_team.routing import route_task
from doom_team.domain import TaskContract
from doom_team.transport import resolve_validation_policy, validation_fingerprint
from doom_team.validation import execute_command


def manifest() -> dict[str, object]:
    return {'version': 1, 'setup': [],
            'build': [{'argv': [sys.executable, '-c', 'print("real build")']}],
            'checks': [{'argv': [sys.executable, '-c', 'print("real checks")']}],
            'test': [{'argv': [sys.executable, '-c', 'assert 1 + 1 == 2']}]}


def test_project_policy_has_native_phases_and_pinned_network_permission() -> None:
    commands = resolve_validation_policy('project')
    assert [c.label for c in commands] == ['dependencies', 'build', 'checks', 'tests']
    assert [c.allow_network for c in commands] == [True, False, False, False]
    assert all('compileall' not in c.argv and 'pytest' not in c.argv for c in commands)
    changed = (replace(commands[0], allow_network=False), *commands[1:])
    assert validation_fingerprint('project', commands) != validation_fingerprint('project', changed)
    # Legacy hashes keep their historical shape.
    assert validation_fingerprint('default', resolve_validation_policy('default'))


@pytest.mark.parametrize('change', ['missing', 'empty-test', 'shell', 'escape', 'noop', 'bad-version'])
def test_missing_or_invalid_native_gates_fail_closed(tmp_path: Path, change: str) -> None:
    data = manifest()
    if change == 'empty-test':
        data['test'] = []
    elif change == 'shell':
        data['test'] = [{'argv': 'npm test'}]
    elif change == 'escape':
        data['build'] = [{'argv': ['node', '--version'], 'cwd': '../outside'}]
    elif change == 'noop':
        data['checks'] = [{'argv': ['true']}]
    elif change == 'bad-version':
        data['version'] = True
    if change != 'missing':
        (tmp_path / 'doom.project.json').write_text(json.dumps(data))
    with pytest.raises(ProjectValidationError):
        load_manifest(tmp_path)


def test_symlink_working_directory_cannot_escape(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'outside').symlink_to(tmp_path, target_is_directory=True)
    data = manifest()
    data['checks'] = [{'argv': ['node', '--version'], 'cwd': 'outside'}]
    (root / 'doom.project.json').write_text(json.dumps(data))
    with pytest.raises(ProjectValidationError, match='symlink'):
        load_manifest(root)


def test_project_executes_real_commands_and_preserves_failure(tmp_path: Path) -> None:
    data = manifest()
    (tmp_path / 'doom.project.json').write_text(json.dumps(data))
    assert run_phase(tmp_path, 'setup') == 0
    assert (tmp_path / '.doom-runtime/python/bin/python').exists()
    assert run_phase(tmp_path, 'build') == run_phase(tmp_path, 'checks') == run_phase(tmp_path, 'test') == 0
    data['test'] = [{'argv': [sys.executable, '-c', 'raise SystemExit(7)']}]
    (tmp_path / 'doom.project.json').write_text(json.dumps(data))
    assert run_phase(tmp_path, 'test') == 7


def test_failed_setup_blocks_later_phases(tmp_path: Path) -> None:
    data = manifest()
    data['setup'] = [{'argv': [sys.executable, '-c', 'raise SystemExit(9)']}]
    (tmp_path / 'doom.project.json').write_text(json.dumps(data))
    assert run_phase(tmp_path, 'setup') == 9
    with pytest.raises(ProjectValidationError, match='setup did not complete'):
        run_phase(tmp_path, 'test')


def test_dependency_network_opt_in_still_scrubs_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'must-not-reach-native-command')
    monkeypatch.setattr('doom_team.validation.network_isolation_prefix', lambda: pytest.fail('Opt-in setup uses network'))
    result = execute_command(
        (sys.executable, '-I', '-c', 'import os; assert "OPENROUTER_API_KEY" not in os.environ; print("scrubbed")'),
        workspace_root=tmp_path, allow_network=True,
    )
    assert result.exit_code == 0 and 'scrubbed' in result.stdout


def test_general_capability_routes_framework_tasks() -> None:
    task = TaskContract(id='react-ui', objective_id='objective', instruction='Implement React + TypeScript UI',
                        acceptance_criteria=('Native test passes',), required_capabilities=('software.engineering',))
    assert route_task(task).specialist_id == 'software_engineer'


@pytest.mark.skipif(not shutil.which('node'), reason='Node runtime required for native-stack integration')
@pytest.mark.parametrize('passing', [True, False])
def test_native_node_mission_requires_real_tests_and_review(tmp_path: Path, passing: bool) -> None:
    node = str(shutil.which('node'))
    data = {'version': 1, 'setup': [],
            'build': [{'argv': [node, '--check', 'app.mjs']}],
            'checks': [{'argv': [node, '--check', 'app.test.mjs']}],
            'test': [{'argv': [node, '--test', 'app.test.mjs']}]}
    files = {'doom.project.json': json.dumps(data),
             'app.mjs': 'export const add = (a, b) => a + b;\n',
             'app.test.mjs': 'import {test} from "node:test"; import assert from "node:assert/strict"; '
                             'import {add} from "./app.mjs"; '
                             f'test("real native test",()=>assert.equal(add(2,3),{5 if passing else 99}));\n',
             '.gitignore': '.doom-runtime/\nnode_modules/\n'}

    class NativeInference:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list[dict[str, str]]) -> SimpleNamespace:
            self.calls += 1
            if self.calls == 1:
                content: dict[str, object] = {'tasks': [{'task_id': 'node-core', 'description': 'Create native Node app and tests',
                    'dependencies': [], 'required_capabilities': ['software.engineering'],
                    'acceptance_criteria': ['Build, checks and native tests pass']}]}
            elif self.calls == 2:
                content = {'files': [{'path': p, 'content': s} for p, s in files.items()], 'summary': 'Native Node project'}
            else:
                assert passing, 'Failing native tests must block before any QA/Reviewer call'
                assert 'native command' in messages[1]['content']
                for label in ('dependencies', 'build', 'checks', 'tests'):
                    assert f'"label": "{label}"' in messages[1]['content']
                content = {'verdict': 'pass', 'summary': 'Real native gates passed', 'blockers': []}
            return SimpleNamespace(content=json.dumps(content))

    client = NativeInference()
    settings = Settings(state_dir=tmp_path / 'state', model_provider='openrouter', model_name='fake',
                        validation_policy='project', semantic_retries=0)
    runner = _build_mission_runner(settings, client)
    result = runner.run('Build Node app with native tests', tmp_path / 'project')
    record = MissionStore(settings.state_dir).load(result.mission_id)
    assert record.validation_fingerprint
    if passing:
        assert record.status == 'awaiting_human_approval' and client.calls == 4
        assert record.final_validation and all(v['passed'] for v in record.final_validation)
        assert record.final_validation_sha == record.final_frontier_sha
        assert record.tasks[0].qa and record.tasks[0].reviewer
    else:
        assert record.status == 'blocked' and client.calls == 2
        assert record.tasks[0].validation[-1]['passed'] is False


def test_config_explicitly_accepts_project_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DOOM_API_KEY', 'test-only-placeholder')
    loaded = ConfigLoader().load(state_dir=str(tmp_path), model_provider='openrouter', model_name='fake',
                                validation_policy='project')
    assert loaded.validation_policy == 'project'


def test_changed_cwd_symlink_is_rechecked_between_commands(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'place').mkdir()
    data = manifest()
    data['setup'] = [
        {'argv': [sys.executable, '-c', 'from pathlib import Path; p=Path("place"); '
                  'p.rmdir(); p.symlink_to("..",target_is_directory=True)']},
        {'argv': [sys.executable, '-c', 'raise SystemExit("should never execute")'], 'cwd': 'place'},
    ]
    (root / 'doom.project.json').write_text(json.dumps(data))
    with pytest.raises(ProjectValidationError, match='outside symlink'):
        run_phase(root, 'setup')


@pytest.mark.parametrize('request_policy', ['default', 'project'])
def test_project_worker_refuses_policy_or_fingerprint_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_policy: str,
) -> None:
    from doom_team import worker
    from doom_team.transport import TransportError
    from tests.test_quality_policy import make_request

    request = replace(make_request(tmp_path, 'wrong-fingerprint'), validation_policy=request_policy)
    settings = Settings(state_dir=tmp_path / 'state', model_provider='openrouter', model_name='fake',
                        validation_policy='project')
    monkeypatch.setattr(worker.ConfigLoader, 'load', lambda self: settings)
    monkeypatch.setattr(worker, 'build_inference_client', lambda *args, **kwargs: SimpleNamespace())
    with pytest.raises(TransportError, match='pinned request'):
        worker.run_attempt(request)
