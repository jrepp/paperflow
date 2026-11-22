"""Bounded, read-only Reddit validation adapter. No persistence or account writes."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from paperflow_settings import RedditSettings


class RedditAccessError(RuntimeError):
    pass


class RedditProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subreddit: str = Field(pattern=r"^[A-Za-z0-9_]{2,21}$")
    sort: Literal["new", "top", "hot"] = "top"
    window: Literal["hour", "day", "week", "month", "year", "all"] = "week"
    limit: int = Field(default=5, ge=1, le=10)
    comments_per_post: int = Field(default=5, ge=0, le=20)


class RedditComment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    parent_id: str
    body: str
    score: int | None
    score_hidden: bool
    permalink: str


class RedditPost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    channel: str
    title: str
    body: str
    permalink: str
    outbound_url: str | None
    published_at: AwareDatetime
    score: int | None
    upvote_ratio: float | None = Field(default=None, ge=0, le=1)
    comment_count: int = Field(ge=0)
    comments: tuple[RedditComment, ...] = ()
    paper_links: tuple[str, ...] = ()
    comments_status: Literal["sampled", "not_requested", "unavailable"] = "not_requested"
    comment_error: str | None = None


class RedditProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["reddit"] = "reddit"
    mode: Literal["live", "fixture"]
    observed_at: AwareDatetime
    channel: str
    posts: tuple[RedditPost, ...]
    next_cursor: str | None = None
    warnings: tuple[str, ...] = ()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RedditTransport:
    """Use an approved application's OAuth bearer token; never log credentials."""

    def __init__(self, settings: RedditSettings):
        if not settings.access_token or not settings.access_token.get_secret_value().strip():
            raise RedditAccessError("Set PAPERFLOW_REDDIT__ACCESS_TOKEN for an approved Reddit application.")
        if not settings.user_agent.strip():
            raise RedditAccessError("Set PAPERFLOW_REDDIT__USER_AGENT to an honest app/version/contact identifier.")
        self.settings = settings
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._remaining: float | None = None

    def __call__(self, path: str, params: dict[str, str | int]) -> object:
        if self._remaining is not None and self._remaining < 1:
            raise RedditAccessError("Reddit rate budget exhausted; retry after the upstream reset.")
        url = "https://oauth.reddit.com/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.settings.access_token.get_secret_value(),
            "User-Agent": self.settings.user_agent,
            "Accept": "application/json",
        })
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                remaining = response.headers.get("X-Ratelimit-Remaining")
                if remaining is not None:
                    self._remaining = float(remaining)
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise RedditAccessError("Reddit response exceeded the probe size limit.")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            messages = {
                401: "Reddit rejected the token; check its validity and expiry.",
                403: "Reddit denied access; check app approval, scopes, and subreddit access.",
                429: "Reddit rate limited the probe; retry after the upstream reset.",
            }
            raise RedditAccessError(messages.get(exc.code, f"Reddit returned HTTP {exc.code}.")) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RedditAccessError("Reddit request failed or returned an invalid response.") from None


def _listing(value: object) -> list[dict]:
    if not isinstance(value, dict) or value.get("kind") != "Listing":
        raise ValueError("expected a Reddit Listing response")
    data = value.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("children"), list):
        raise ValueError("Reddit Listing has no children array")
    return data["children"]


def _paper_links(text: str) -> tuple[str, ...]:
    urls = re.findall(r"https?://[^\s<>\]\)\"']+", text)
    return tuple(sorted({url.rstrip(".,;") for url in urls
                         if urllib.parse.urlsplit(url).hostname in
                         {"arxiv.org", "www.arxiv.org", "doi.org", "dx.doi.org"}}))


def _comments(payload: object, post_id: str, limit: int) -> tuple[RedditComment, ...]:
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("expected Reddit post and comment listings")
    result = []
    # A bounded top-level sample. Do not recursively expand replies or 'more'.
    for child in _listing(payload[1]):
        if child.get("kind") != "t1":
            continue
        data = child["data"]
        if data.get("parent_id") != post_id or data.get("body") in (None, "[deleted]", "[removed]"):
            continue
        hidden = bool(data.get("score_hidden", False))
        result.append(RedditComment(
            source_id=data["name"], parent_id=data["parent_id"], body=data["body"],
            score=None if hidden else data.get("score"), score_hidden=hidden,
            permalink="https://www.reddit.com" + data["permalink"],
        ))
        if len(result) >= limit:
            break
    return tuple(result)


def probe_reddit(request: RedditProbeRequest,
                 fetch: Callable[[str, dict[str, str | int]], object], *,
                 mode: Literal["live", "fixture"] = "live") -> RedditProbeResult:
    params: dict[str, str | int] = {"limit": request.limit, "raw_json": 1}
    if request.sort == "top":
        params["t"] = request.window
    payload = fetch(f"r/{request.subreddit}/{request.sort}", params)
    children = _listing(payload)
    posts = []
    warnings = []
    seen = set()
    for child in children:
        if child.get("kind") != "t3":
            continue
        data = child["data"]
        source_id = data["name"]
        if not re.fullmatch(r"t3_[a-z0-9]+", source_id):
            raise ValueError("invalid Reddit post identity")
        if data.get("subreddit", "").lower() != request.subreddit.lower():
            raise ValueError("Reddit response belongs to a different subreddit")
        if source_id in seen or data.get("removed_by_category") or data.get("selftext") in ("[removed]", "[deleted]"):
            continue
        seen.add(source_id)
        comments = ()
        status = "not_requested"
        error = None
        if request.comments_per_post:
            try:
                comments = _comments(fetch(f"comments/{source_id[3:]}", {
                    "sort": "top", "limit": request.comments_per_post, "depth": 1, "raw_json": 1,
                }), source_id, request.comments_per_post)
                status = "sampled"
            except (RedditAccessError, ValueError, KeyError, TypeError):
                status = "unavailable"
                error = "Comment sample unavailable; access, rate limits, or response shape may have changed."
                warnings.append(f"{source_id}: {error}")
        body = data.get("selftext") or ""
        outbound = None if data.get("is_self") else data.get("url")
        posts.append(RedditPost(
            source_id=source_id, channel=f"reddit:{request.subreddit.lower()}",
            title=data["title"], body=body, permalink="https://www.reddit.com" + data["permalink"],
            outbound_url=outbound, published_at=datetime.fromtimestamp(data["created_utc"], UTC),
            score=data.get("score"), upvote_ratio=data.get("upvote_ratio"),
            comment_count=data.get("num_comments", 0), comments=comments,
            comments_status=status, comment_error=error,
            paper_links=_paper_links("\n".join([body, outbound or "", *(c.body for c in comments)])),
        ))
        if len(posts) >= request.limit:
            break
    return RedditProbeResult(mode=mode, observed_at=datetime.now(UTC),
                             channel=f"reddit:{request.subreddit.lower()}", posts=tuple(posts),
                             next_cursor=payload["data"].get("after"), warnings=tuple(warnings))


def fixture_transport(bundle: dict) -> Callable[[str, dict[str, str | int]], object]:
    """Fixture format: listing plus comments keyed by bare post ID; never fetches."""
    def fetch(path: str, params: dict[str, str | int]) -> object:
        if path.startswith("r/"):
            return bundle["listing"]
        return bundle["comments"][path.split("/")[1]]
    return fetch
