"""Bridge Docuchango references to collected, passing pytest journeys."""
from pathlib import Path
import argparse
import json
import sys
import subprocess

import pytest
from docuchango.traceability import parse_traceability

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def coverage(root=ROOT):
    rows = json.loads((root/'tests/acceptance/coverage.json').read_text())
    definitions = parse_traceability((root/'docs/plans/reader-acceptance.md').read_text().splitlines()).definitions
    criteria = {key for key in definitions if key.startswith('AC-')}
    assert len(rows) == len(criteria) and {r['criterion'] for r in rows} == criteria, 'Acceptance coverage must account for every criterion exactly once'
    for row in rows:
        assert row['requirement'] in definitions[row['criterion']][0].requirements
        assert row['disposition'] in {'covered', 'unresolved', 'deferred'}
        assert row['reason']
        assert bool(row['test']) == (row['disposition'] == 'covered')
    table = '| Criterion | Requirement | Disposition | Test | Browser test | Evidence or gap |\n| --- | --- | --- | --- | --- | --- |\n'
    table += ''.join(f"| reader-acceptance::{r['criterion']} | reader-acceptance::{r['requirement']} | {r['disposition']} | {r['test'] or '—'} | {r.get('browser_test') or '—'} | {r['reason']} |\n" for r in rows)
    doc = (root/'docs/validation/reader-acceptance.md').read_text()
    actual = doc.split('<!-- acceptance-coverage:start -->\n')[1].split('<!-- acceptance-coverage:end -->')[0]
    assert actual == table, 'Acceptance document and coverage manifest differ'
    return rows


class AcceptanceGate:
    def __init__(self, rows):
        self.expected = {row['test'] for row in rows if row['disposition'] == 'covered'}
        self.collected = set()
        self.passed = set()
        self.failed = set()

    def pytest_collection_modifyitems(self, items):
        self.collected = {item.nodeid for item in items}

    def pytest_runtest_logreport(self, report):
        if report.nodeid not in self.expected:
            return
        if report.failed or report.skipped or hasattr(report, 'wasxfail'):
            self.failed.add(report.nodeid)
        elif report.when == 'call' and report.passed:
            self.passed.add(report.nodeid)

    def missing(self):
        return self.expected - (self.collected & self.passed) | self.failed


def browser_results(report):
    passed = set()
    def visit(suite):
        for spec in suite.get('specs', []):
            tests = spec.get('tests', [])
            if tests and all(test.get('expectedStatus') == 'passed' and test.get('results') and
                             all(result.get('status') == 'passed' for result in test['results']) for test in tests):
                passed.add(f"{spec['file']}::{spec['title']}")
        for child in suite.get('suites', []):
            visit(child)
    for suite in report.get('suites', []):
        visit(suite)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-only', action='store_true', help='Unit-check mode; does not establish browser acceptance')
    parser.add_argument('--suite', action='store_true', help='Run all non-Docker tests')
    parser.add_argument('--require-complete', action='store_true', help='Fail for unresolved or deferred acceptance criteria')
    args = parser.parse_args()
    rows = coverage()
    gate = AcceptanceGate(rows)
    result = pytest.main(['tests' if args.suite else 'tests/acceptance', '--ignore=tests/integration', '-q'], plugins=[gate])
    missing = gate.missing()
    browser_expected = {row['browser_test'] for row in rows if row.get('browser_test')}
    browser_missing = set()
    if not args.backend_only:
        completed = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test', '--reporter=json'], cwd=ROOT, capture_output=True, text=True)
        try:
            browser_missing = browser_expected - browser_results(json.loads(completed.stdout))
        except (ValueError, KeyError):
            browser_missing = browser_expected
        if completed.returncode or browser_missing:
            print(completed.stderr)
            print(completed.stdout)
            result = int(result) or 1
        else:
            print(f'Browser acceptance: {len(browser_expected)} linked journey passed; full browser suite passed.')
    elif browser_expected:
        print('Backend-only check: browser acceptance was not run.')
    gaps = [r['criterion'] for r in rows if r['disposition'] != 'covered']
    print(f'Acceptance: {len(gate.expected - missing)}/{len(gate.expected)} linked tests passed; open criteria: {", ".join(gaps) or "none"}')
    if missing:
        print('Missing or unsuccessful acceptance tests: ' + ', '.join(sorted(missing)))
    return int(result) or int(bool(missing or browser_missing) or (args.require_complete and (bool(gaps) or args.backend_only)))


if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    raise SystemExit(main())
