"""Deterministic inventory for the packaged, reproducibly built browser assets."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[1]/'paperflow_reader/static'
for path in sorted(root.rglob('*')):
    if path.is_file():
        print(f'{path.relative_to(root)} {hashlib.sha256(path.read_bytes()).hexdigest()}')
