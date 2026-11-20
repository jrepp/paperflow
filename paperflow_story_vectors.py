"""Rebuildable, local story search using pinned English embeddings and Memvid."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
import json
import logging
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

MODEL = "minishlab/potion-base-2M"
REVISION = "389b9f64be5aa4ae7a6bc6fe95ef20ce485ae5da"
IDENTITY = f"{MODEL}@{REVISION}"
ASSETS = {
    "config.json": "b2a89173391ca774c2d7323090a993a9a1553faa5b40eb37bb7cec6685fbea47",
    "model.safetensors": "f95ffde02ad06f63ae38eb9d400038cd5ccaf8411ec3cb650c6025113f96cbb8",
    "tokenizer.json": "e67e803f624fb4d67dea1c730d06e1067e1b14d830e2c2202569e3ef0f70bb50",  # gitleaks:allow pinned public SHA-256
    "README.md": "38af170acce93b5eb0c4e18659ab0b30581b2f6860f3e12d8ba8b8751e249363",
}


def model_directory():
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(os.environ.get("PAPERFLOW_STORY_MODEL_DIR", cache / "paperflow" / REVISION))


def prepare_model(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, digest in ASSETS.items():
        dest = directory / name
        if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
            continue
        with urlopen(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=45) as response:
            data = response.read(16 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Invalid story embedding asset {name}")
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as f:
            f.write(data)
        os.chmod(f.name, 0o644)
        os.replace(f.name, dest)


@lru_cache(maxsize=1)
def _model():
    from model2vec import StaticModel
    directory = model_directory()
    prepare_model(directory)
    return StaticModel.from_pretrained(directory)


def _append(index, documents):

    entries = list(documents.items())
    for start in range(0, len(entries), 128):
        chunk = entries[start:start + 128]
        requests = [dict(title=story_id, text=text, label="story", uri=uri)
                    for uri, (story_id, text) in chunk]
        vectors = _model().encode([entry["text"] for entry in requests])
        index.put_many(requests, embeddings=vectors.tolist(), embedding_identity={
            "model": IDENTITY, "provider": "model2vec", "dimension": vectors.shape[1],
        })
    index.seal()


def _build_parts(directory, week, documents):
    """Seal compact snapshots, splitting the hash-ordered inputs if capacity is tight."""
    import memvid_sdk

    digest = hashlib.sha256(json.dumps(list(documents), separators=(',', ':')).encode()).hexdigest()
    filename = f'{week}-{digest}.mv2'
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        path = Path(tmp) / filename
        index = memvid_sdk.create(str(path), enable_vec=True, enable_lex=False)
        try:
            _append(index, documents)
            stats = index.stats()
            split = len(documents) > 1 and stats['remaining_capacity_bytes'] < stats['capacity_bytes'] // 4
        except memvid_sdk.CapacityExceededError:
            if len(documents) == 1:
                raise
            split = True
        finally:
            index.close()
        if not split:
            os.replace(path, directory / filename)
            return [dict(file=filename, stories=len(documents), remaining_bytes=stats['remaining_capacity_bytes'])]
    entries = list(documents.items())
    mid = len(entries) // 2
    return (_build_parts(directory, week, dict(entries[:mid])) +
            _build_parts(directory, week, dict(entries[mid:])))


def _part_hits(directory, record, entries, query, embedding):
    import memvid_sdk

    hits = set()
    for part in record['parts']:
        index = memvid_sdk.use('basic', str(directory / part['file']), enable_vec=True, enable_lex=False)
        try:
            result = index.find(query, mode='semantic', k=min(8, part['stories']),
                                query_embedding=embedding.tolist(), query_embedding_model=IDENTITY)
            hits.update(entries[hit['uri']][0] for hit in result['hits'])
        finally:
            index.close()
    return hits


def _search_snapshot(directory, bins, query, embedding):
    import memvid_sdk

    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'manifest.json'
    with (directory / 'index.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(manifest.read_text()) if manifest.exists() else {}
        current = {}
        for week, entries in sorted(bins.items()):
            ordered = sorted(entries, key=lambda uri: hashlib.sha256(entries[uri][0].encode()).digest())
            digest = hashlib.sha256(json.dumps(ordered).encode()).hexdigest()
            record = previous.get(week, {})
            if record.get('digest') != digest or any(not (directory / p['file']).exists() for p in record['parts']):
                parts = _build_parts(directory, week, {uri: entries[uri] for uri in ordered})
                record = dict(digest=digest, parts=parts, rebuilt_at=datetime.now(UTC).isoformat())
                logging.getLogger(__name__).info('story index rebuilt week=%s parts=%s', week, parts)
            current[week] = record
        hits = set()
        for week, record in current.items():
            entries = bins[week]
            try:
                hits.update(_part_hits(directory, record, entries, query, embedding))
            except (memvid_sdk.CorruptFileError, memvid_sdk.VerifyFailedError):
                ordered = sorted(entries, key=lambda uri: hashlib.sha256(entries[uri][0].encode()).digest())
                parts = _build_parts(directory, week, {uri: entries[uri] for uri in ordered})
                record = dict(digest=record['digest'], parts=parts, rebuilt_at=datetime.now(UTC).isoformat())
                current[week] = record
                hits.update(_part_hits(directory, record, entries, query, embedding))
        if current != previous:
            staged = manifest.with_suffix('.tmp')
            staged.write_text(json.dumps(current, sort_keys=True))
            os.replace(staged, manifest)
        live = {part['file'] for record in current.values() for part in record['parts']}
        for obsolete in directory.glob('*.mv2'):
            if obsolete.name not in live:
                obsolete.unlink()

        return hits


def semantic_candidates(query, documents, *, index_path: Path, weeks=4, as_of=None):
    """Search public story snapshots in the N UTC calendar weeks ending at as_of."""
    import numpy as np

    if not query.strip():
        return []
    today = (as_of or datetime.now(UTC)).astimezone(UTC).date()
    monday = today - timedelta(days=today.weekday())
    first_week = monday - timedelta(weeks=weeks - 1)
    bins = {}
    story_text = {}
    for story_id, text, material_at in documents:
        day = material_at.astimezone(UTC).date()
        week = day - timedelta(days=day.weekday())
        if first_week <= week <= monday:
            text = text[:12000]
            digest = hashlib.sha256((IDENTITY + '\n' + text).encode()).hexdigest()
            bins.setdefault(week.isoformat(), {})[f'story:{story_id}/{digest}'] = (story_id, text)
            story_text[story_id] = text

    embedding = _model().encode([query[:12000]])[0] if bins else None
    hits = _search_snapshot(index_path.with_suffix('.weeks'), bins, query, embedding)
    if not hits:
        return []
    ids = sorted(hits)
    vectors = _model().encode([story_text[key] for key in ids])
    norms = np.linalg.norm(vectors, axis=1) * np.linalg.norm(embedding)
    scores = np.divide(vectors @ embedding, norms, out=np.zeros(len(ids)), where=norms > 0)
    return [ids[i] for i in np.argsort(-scores, kind='stable')[:8]]


if __name__ == "__main__":
    prepare_model(model_directory())
