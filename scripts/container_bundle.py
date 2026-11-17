#!/usr/bin/env python3
"""Build, test and export the exact checked-out commit as a deployable bundle."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import UTC, datetime


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    channel = sys.argv[1]
    assert channel in {'nightly', 'release'}
    sha = run('git', 'rev-parse', 'HEAD')
    built = datetime.now(UTC).isoformat()
    version = Path('VERSION').read_text().strip()
    image = 'paperflow:' + sha
    subprocess.run(['docker', 'build', '--build-arg', 'BUILD_COMMIT='+sha,
                    '--build-arg', 'BUILD_TIME='+built, '--build-arg', 'BUILD_CHANNEL='+channel,
                    '-t', image, '-f', 'Containerfile', '.'], check=True)
    actual = json.loads(run('docker', 'run', '--rm', '--network', 'none', image,
                           'python', '-c', 'import json; from paperflow_build import build_info; print(json.dumps(build_info()))'))
    assert actual == {'version': version, 'commit': sha, 'built_at': built, 'channel': channel}
    # Exercise real HTTP health and identity in an isolated API-only container.
    run('docker', 'run', '-d', '--name', 'paperflow-smoke', '-p', '127.0.0.1:18000:8000',
        '-e', 'PAPERFLOW_MONITOR__ENABLED=false', '-e', 'PAPERFLOW_STORAGE__PATH=/tmp/smoke.db',
        image, 'python', '-c', 'import uvicorn; from paperflow_api import create_app; from paperflow_settings import ResearchSettings; uvicorn.run(create_app(ResearchSettings()), host="0.0.0.0", port=8000)')
    try:
        import time
        from urllib.request import urlopen
        for attempt in range(30):
            try:
                with urlopen('http://127.0.0.1:18000/health/ready', timeout=2) as r:
                    assert json.load(r)['healthy']
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(1)
        with urlopen('http://127.0.0.1:18000/api/version', timeout=2) as r:
            assert json.load(r) == actual
    finally:
        run('docker', 'rm', '-f', 'paperflow-smoke')
    out = Path('artifacts/container')
    out.mkdir(parents=True, exist_ok=True)
    archive = out/'paperflow-image.tar.gz'
    with gzip.open(archive, 'wb', compresslevel=1) as target:
        process = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
        import shutil
        shutil.copyfileobj(process.stdout, target)
        assert process.wait() == 0
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    manifest = {**actual, 'schema': 1, 'archive': archive.name, 'sha256': digest,
                'image_id': run('docker', 'image', 'inspect', '--format', '{{.Id}}', image)}
    (out/'deployment.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
