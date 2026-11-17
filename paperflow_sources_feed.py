"""One RSS/Atom discovery component, shared by platform-specific channel configs."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

import feedparser

from paperflow_ingest_models import ChannelConfig, SourceEntry


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html: str) -> str:
    parser = _PlainText()
    parser.feed(html)
    return "".join(parser.parts).strip()


class FeedDiscovery:
    def fetch(self, channel: ChannelConfig, get: Callable[[str], bytes]) -> tuple[list[SourceEntry], list[str]]:
        parsed = feedparser.parse(get(str(channel.url)))
        if not parsed.get("version"):
            raise ValueError("response is not an RSS/Atom feed")
        warnings = ["Feed contains recoverable parse errors"] if parsed.get("bozo") else []
        entries = []
        seen = set()
        for entry in parsed.entries[:channel.limit]:
            link = urljoin(str(channel.url), entry.get("link", "")) if entry.get("link") else None
            if not link or urlsplit(link).scheme not in {"http", "https"} or not entry.get("title", "").strip():
                warnings.append("Skipped feed entry without a usable title and HTTP(S) link")
                continue
            identity = entry.get("id") or link
            if identity in seen:
                continue
            seen.add(identity)
            date = entry.get("published_parsed") or entry.get("updated_parsed")
            comments = entry.get("comments")
            if comments:
                comments = urljoin(str(channel.url), comments)
                if urlsplit(comments).scheme not in {"http", "https"}:
                    comments = None
            # Absolute feed IDs often identify the discussion while link points
            # at an external article (e.g. Tildes Atom and HN RSS).
            if not comments and identity != link and urlsplit(identity).scheme in {"http", "https"}:
                comments = identity
            entries.append(SourceEntry(
                source_id=identity, title=plain_text(entry.title), url=link,
                discussion_url=comments,
                published_at=datetime.fromtimestamp(calendar.timegm(date), UTC) if date else None,
                excerpt=plain_text(entry.get("summary", "")),
            ))
        return entries, warnings
