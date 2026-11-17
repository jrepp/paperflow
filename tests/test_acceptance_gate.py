"""Coverage claims must survive collection and execution, not just documentation lint."""
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.acceptance import AcceptanceGate, coverage


def test_manifest_matches_native_definitions_and_document():
    assert len(coverage()) == 6


@pytest.mark.parametrize('outcome', ['missing', 'skipped', 'failed', 'xfail', 'teardown', 'passed'])
def test_gate_requires_collected_success_without_skips(outcome):
    gate = AcceptanceGate([{'test': 'test_journey', 'disposition': 'covered'}])
    if outcome != 'missing':
        gate.pytest_collection_modifyitems([SimpleNamespace(nodeid='test_journey')])
        report = SimpleNamespace(nodeid='test_journey', when='call', passed=outcome in {'passed','teardown'}, failed=outcome == 'failed', skipped=outcome in {'skipped','xfail'})
        if outcome == 'xfail':
            report.wasxfail = 'Not implemented'
        gate.pytest_runtest_logreport(report)
        if outcome == 'teardown':
            gate.pytest_runtest_logreport(SimpleNamespace(nodeid='test_journey', when='teardown', passed=False, failed=True, skipped=False))
    assert gate.missing() == (set() if outcome == 'passed' else {'test_journey'})


def test_browser_gate_rejects_missing_skipped_and_expected_failures():
    from scripts.acceptance import browser_results
    report = {'suites':[{'specs':[
        {'file':'reader.spec.js','title':'passes','tests':[{'expectedStatus':'passed','results':[{'status':'passed'}]}]},
        {'file':'reader.spec.js','title':'skips','tests':[{'expectedStatus':'skipped','results':[{'status':'skipped'}]}]},
        {'file':'reader.spec.js','title':'xfail','tests':[{'expectedStatus':'failed','results':[{'status':'failed'}]}]},
        {'file':'reader.spec.js','title':'flaky','tests':[{'expectedStatus':'passed','results':[{'status':'failed'},{'status':'passed'}]}]},
    ]}]}
    assert browser_results(report) == {'reader.spec.js::passes'}
