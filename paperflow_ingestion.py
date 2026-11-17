"""Compose discovery, optional enrichment, and persistence without source switches."""
from __future__ import annotations

import hashlib
from typing import Callable, Protocol
from uuid import uuid5

from paperflow_domain import ContentRevision, Identifier, Item
from paperflow_http import get_bytes
from paperflow_ingest_models import ChannelConfig, IngestBatch, SourceEntry, SourceObservation, SourceRemovedError
from paperflow_sources_feed import FeedDiscovery
from paperflow_sources_hackernews import HackerNewsEnricher
from paperflow_sources_huggingface import HuggingFacePapersDiscovery
from paperflow_storage import ResearchStorage, ResearchRepository

Transport = Callable[[str], bytes]


class Discovery(Protocol):
    def fetch(self, channel: ChannelConfig, get: Transport) -> tuple[list[SourceEntry], list[str]]: ...


class Enricher(Protocol):
    def enrich(self, entry: SourceEntry, channel: ChannelConfig, get: Transport) -> SourceEntry: ...


class IntegrationRegistry:
    def __init__(self):
        self.discovery: dict[str, Discovery] = {}
        self.enrichers: dict[str, Enricher] = {}

    def collect(self, name: str, channel: ChannelConfig, get: Transport = get_bytes,
                progress: Callable[[str, int, int], None] | None = None) -> IngestBatch:
        # Validate the complete composition before doing network work.
        if channel.discovery not in self.discovery:
            raise ValueError(f"unknown discovery component: {channel.discovery}")
        for key in channel.enrichers:
            if key not in self.enrichers:
                raise ValueError(f"unknown enrichment component: {key}")
        if progress:
            progress("discovery", 0, 0)
        entries, warnings = self.discovery[channel.discovery].fetch(channel, get)
        results = []
        for index, entry in enumerate(entries):
            if progress:
                progress("enrichment", index, len(entries))
            removed = False
            for key in channel.enrichers:
                try:
                    entry = self.enrichers[key].enrich(entry, channel, get)
                except SourceRemovedError:
                    warnings.append(f"{key}: unavailable item omitted")
                    removed = True
                    break
                except (OSError, ValueError, TypeError):
                    warnings.append(f"{key}: enrichment unavailable for {entry.source_id}")
            if not removed:
                results.append(entry)
        if progress:
            progress("enrichment", len(entries), len(entries))
        return IngestBatch(channel=name, config=channel, entries=tuple(results), warnings=tuple(warnings))


def default_registry() -> IntegrationRegistry:
    registry = IntegrationRegistry()
    registry.discovery["feed"] = FeedDiscovery()
    registry.discovery["huggingface-papers"] = HuggingFacePapersDiscovery()
    registry.enrichers["hackernews"] = HackerNewsEnricher()
    return registry


def persist_batch(storage: ResearchStorage, batch: IngestBatch) -> int:
    """Atomic, replayable import; feed-scoped identity avoids accidental merges.

    No checkpoint is advanced: this first adapter polls a bounded feed window.
    Source observations preserve changes independently of immutable item identity.
    """
    with storage.transaction() as repo:
        return persist_batch_in_repository(repo, batch)


def persist_batch_in_repository(repo: ResearchRepository, batch: IngestBatch) -> int:
    """Import using a caller-owned transaction (e.g. run completion + content)."""
    namespace = "feed:" + hashlib.sha256(str(batch.config.url).encode()).hexdigest()
    for entry in batch.entries:
        item = repo.resolve_identifier(namespace, entry.source_id)
        if item is None:
            item = repo.add(Item(kind=batch.config.kind, title=entry.title,
                                 canonical_url=(entry.url if batch.config.kind == "paper"
                                                else entry.discussion_url or entry.url),
                                 published_at=entry.published_at))
            repo.add(Identifier(item_id=item.id, namespace=namespace, value=entry.source_id))
        observation_id = uuid5(batch.id, entry.source_id)
        observation = SourceObservation(id=observation_id, created_at=batch.created_at,
            item_id=item.id, batch_id=batch.id, channel=batch.channel,
            feed_url=batch.config.url, entry=entry, observed_at=batch.fetched_at)
        repo.add(observation)
        if entry.excerpt:
            revision = ContentRevision.from_markdown(item_id=item.id, markdown=entry.excerpt,
                                                     processor_version="feed-excerpt-v1", evidence_basis="excerpt")
            revision_id = uuid5(item.id, "feed-excerpt-v1:" + revision.content_hash)
            if repo.get(ContentRevision, revision_id) is None:
                repo.add(revision.model_copy(update={"id": revision_id}))
    return len(batch.entries)
