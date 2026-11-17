"""Optional official HN API enrichment for records discovered through a feed."""
from __future__ import annotations

import json
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from paperflow_ingest_models import ChannelConfig, CommentSample, Signals, SourceEntry, SourceRemovedError
from paperflow_sources_feed import plain_text

API = "https://hacker-news.firebaseio.com/v0"


class HackerNewsEnricher:
    def enrich(self, entry: SourceEntry, channel: ChannelConfig, get: Callable[[str], bytes]) -> SourceEntry:
        url = urlsplit(str(entry.discussion_url or entry.url))
        values = parse_qs(url.query).get("id", [])
        if url.hostname != "news.ycombinator.com" or url.path != "/item" or not values or not values[0].isdigit():
            raise ValueError("HN enrichment requires a news.ycombinator.com/item?id= link")
        story_id = int(values[0])
        data = json.loads(get(f"{API}/item/{story_id}.json"))
        if not data or data.get("deleted") or data.get("dead"):
            raise SourceRemovedError("HN item is deleted or dead")
        if data.get("id") != story_id or data.get("type") != "story":
            raise ValueError("HN API returned a different item or unsupported type")
        comments = []
        unavailable = False
        for comment_id in data.get("kids", [])[:channel.comment_limit]:
            if not isinstance(comment_id, int):
                raise ValueError("invalid HN comment identity")
            try:
                comment = json.loads(get(f"{API}/item/{comment_id}.json"))
                if not comment or comment.get("deleted") or comment.get("dead"):
                    continue
                if comment.get("id") != comment_id or comment.get("type") != "comment" or comment.get("parent") != story_id:
                    raise ValueError("comment belongs to another story")
                comments.append(CommentSample(source_id=str(comment_id),
                    url=f"https://news.ycombinator.com/item?id={comment_id}",
                    text=plain_text(comment.get("text", ""))))
            except (OSError, ValueError, TypeError):
                unavailable = True
        return entry.model_copy(update={
            "signals": Signals(score=data.get("score"), comment_count=data.get("descendants")),
            "comments": tuple(comments),
            "comments_status": "unavailable" if unavailable else "sampled" if channel.comment_limit else "not_requested",
        })
