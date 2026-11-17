"""Exercise the required aggregate check's fail-closed behavior."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml


@pytest.mark.parametrize('checks,integration,passed', [
    ('success', 'success', True),
    ('failure', 'success', False),
    ('success', 'failure', False),
    ('cancelled', 'success', False),
    ('success', 'cancelled', False),
    ('skipped', 'success', False),
    ('success', 'skipped', False),
    ('', 'success', False),
])
def test_required_status_fails_for_incomplete_validation(checks, integration, passed):
    workflow = yaml.load(Path('.github/workflows/validate.yml').read_text(), Loader=yaml.BaseLoader)
    gate = workflow['jobs']['status']
    assert gate['if'] == 'always()'
    assert set(gate['needs']) == {'checks', 'integration'}
    result = subprocess.run(['bash', '-e', '-c', gate['steps'][0]['run']],
        env={**os.environ, 'CHECKS_RESULT': checks, 'INTEGRATION_RESULT': integration},
        capture_output=True)
    assert (result.returncode == 0) is passed
