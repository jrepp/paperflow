"""Rebuildable, local story search using pinned English embeddings and Memvid."""
from __future__ import annotations

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


def semantic_candidates(query: str, documents: list[tuple[str, str]], *, index_path: Path) -> list[str]:
    """Update changed rows and return the nearest current story IDs. One monitor owns writes."""
    import memvid_sdk

    if not documents or not query.strip():
        return []
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    for story_id, text in documents:
        text = text[:12000]
        digest = hashlib.sha256((IDENTITY + "\n" + text).encode()).hexdigest()
        current[f"story:{story_id}/{digest}"] = (str(story_id), text)
    active = set()
    index = None
    try:
        if index_path.exists():
            try:
                index = memvid_sdk.use("basic", str(index_path), enable_vec=True, enable_lex=False)
                count = index.stats()["frame_count"]
                active = {entry["uri"] for entry in index.timeline(limit=count)} if count else set()
            except (memvid_sdk.CorruptFileError, memvid_sdk.VerifyFailedError):
                if index is not None:
                    index.close()
                index = None
        stale = active - current.keys()
        if index is None or len(stale) > max(32, len(current) // 4):
            if index:
                index.close()
                index = None
            with tempfile.TemporaryDirectory(dir=index_path.parent) as tmp:
                path = Path(tmp) / "stories.mv2"
                new = memvid_sdk.create(str(path), enable_vec=True, enable_lex=False)
                try:
                    _append(new, current)
                finally:
                    new.close()
                os.replace(path, index_path)
            index = memvid_sdk.use("basic", str(index_path), enable_vec=True, enable_lex=False)
            stale = set()
        elif additions := current.keys() - active:
            _append(index, {uri: current[uri] for uri in sorted(additions)})
        result = index.find(query, mode="semantic", k=min(len(current) + len(stale), 8 + len(stale)),
                            query_embedding=_model().encode([query[:12000]])[0].tolist(),
                            query_embedding_model=IDENTITY)
        return [current[hit["uri"]][0] for hit in result["hits"] if hit["uri"] in current][:8]
    finally:
        if index is not None:
            index.close()


if __name__ == "__main__":
    prepare_model(model_directory())
