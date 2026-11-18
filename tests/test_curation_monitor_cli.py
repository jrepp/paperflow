from typer.testing import CliRunner

from paperflow_cli import app
from paperflow_api_client import APIClient, APIError


def test_curation_stats_and_run_use_monitor_api(monkeypatch):
    calls = []
    def request(self, method, path, body=None):
        calls.append((method, path))
        if method == 'POST':
            return {'status': 'requested', 'coalesced': False}
        return {'enabled': True, 'counts': {'queued': 3, 'failed': 1},
                'diagnostics': {'queue_blocked_reason': 'daily_limit'},
                'spend': {'input_tokens': 1200, 'output_tokens': 300, 'cache_read_tokens': 200,
                          'estimated_usd': '0.25', 'unpriced_requests': 1, 'completed_jobs': 2,
                          'retry_estimated_usd': '0.03', 'by_role_and_model': {}}}
    monkeypatch.setattr(APIClient, 'request', request)
    runner = CliRunner()
    result = runner.invoke(app, ['curation', '--api-url', 'http://localhost:8000', 'stats', '--days', '30'])
    assert result.exit_code == 0, result.output
    assert '1,200 input' in result.output and '$0.25' in result.output and 'daily_limit' in result.output
    assert calls[-1] == ('GET', '/api/monitor/curation?days=30')
    result = runner.invoke(app, ['curation', 'run'])
    assert result.exit_code == 0 and 'requested' in result.output
    assert calls[-1] == ('POST', '/api/monitor/curation/runs')
    monkeypatch.setattr(APIClient, 'request', lambda *args: (_ for _ in ()).throw(APIError('unavailable')))
    result = runner.invoke(app, ['curation', 'stats'])
    assert result.exit_code == 1 and 'unavailable' in result.output
