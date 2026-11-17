"""Public build identity; no host names, credentials, or local paths."""
from importlib.metadata import version
import os


def build_info():
    return {'version': version('paperflow'),
            'commit': os.environ.get('PAPERFLOW_BUILD_COMMIT') or None,
            'built_at': os.environ.get('PAPERFLOW_BUILD_TIME') or None,
            'channel': os.environ.get('PAPERFLOW_BUILD_CHANNEL', 'local')}
