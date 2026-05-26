from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from paperflow_http import get_json
from paperflow_radar import (
    normalize_arxiv_id,
    sanitize_filename,
    single_category_report,
    storage_target_path,
)
from paperflow_sources import normalize_record_identity


HF_DAILY_PAPERS_API_URL = "https://huggingface.co/api/daily_papers"
HF_PAPERS_URL = "https://huggingface.co/papers"


@dataclass
class HuggingFacePaperEntry:
    paper_id: str
    title: str
    summary: str
    authors: list[str]
    published: str
    submitted_at: str
    upvotes: int
    github_stars: int | None
    num_comments: int
    abs_url: str
    pdf_url: str
    paper_url: str
    project_page: str
    github_repo: str
    organization: str
    ai_summary: str
    ai_keywords: list[str]
    suggested_filename: str


def _http_get_json(url: str) -> object:
    return get_json(url)


def _author_names(raw_authors: list[dict]) -> list[str]:
    names = []
    for author in raw_authors:
        name = str(author.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _iso(value: str) -> str:
    if not value:
        return ""
    return value.replace(".000Z", "Z")


def parse_daily_paper(item: dict) -> HuggingFacePaperEntry:
    paper = item.get("paper") if isinstance(item.get("paper"), dict) else item
    paper_id = str(paper.get("id") or item.get("id") or "").strip()
    title = " ".join(str(paper.get("title") or item.get("title") or "").split())
    summary = " ".join(str(paper.get("summary") or item.get("summary") or "").split())
    organization_raw = paper.get("organization") or item.get("organization") or {}
    organization = ""
    if isinstance(organization_raw, dict):
        organization = str(
            organization_raw.get("fullname") or organization_raw.get("name") or ""
        ).strip()
    abs_url = f"https://arxiv.org/abs/{paper_id}" if paper_id else ""
    pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf" if paper_id else ""
    return HuggingFacePaperEntry(
        paper_id=paper_id,
        title=title,
        summary=summary,
        authors=_author_names(list(paper.get("authors") or [])),
        published=_iso(str(paper.get("publishedAt") or item.get("publishedAt") or "")),
        submitted_at=_iso(str(paper.get("submittedOnDailyAt") or "")),
        upvotes=int(paper.get("upvotes") or 0),
        github_stars=(
            int(paper["githubStars"]) if paper.get("githubStars") is not None else None
        ),
        num_comments=int(item.get("numComments") or 0),
        abs_url=abs_url,
        pdf_url=pdf_url,
        paper_url=f"{HF_PAPERS_URL}/{paper_id}" if paper_id else "",
        project_page=str(paper.get("projectPage") or ""),
        github_repo=str(paper.get("githubRepo") or ""),
        organization=organization,
        ai_summary=str(paper.get("ai_summary") or ""),
        ai_keywords=[str(keyword) for keyword in (paper.get("ai_keywords") or [])],
        suggested_filename=f"{sanitize_filename(title)}.pdf",
    )


def fetch_daily_papers(date: str | None = None) -> list[HuggingFacePaperEntry]:
    query = urllib.parse.urlencode({"date": date}) if date else ""
    url = HF_DAILY_PAPERS_API_URL if not query else f"{HF_DAILY_PAPERS_API_URL}?{query}"
    payload = _http_get_json(url)
    if not isinstance(payload, list):
        raise ValueError("Hugging Face daily papers API returned a non-list payload")
    return [parse_daily_paper(item) for item in payload if isinstance(item, dict)]


def paper_to_record(
    entry: HuggingFacePaperEntry,
    *,
    storage_root: str,
    target_path: str,
) -> dict:
    arxiv_id = normalize_arxiv_id(entry.paper_id)
    record = {
        "arxiv_id": arxiv_id,
        "paper_key": f"arxiv:{arxiv_id}" if arxiv_id else "",
        "resolved_id": entry.paper_id,
        "title": entry.title,
        "authors": entry.authors,
        "published": entry.published,
        "updated": entry.published,
        "primary_category": "huggingface_papers",
        "abs_url": entry.abs_url,
        "pdf_url": entry.pdf_url,
        "summary": entry.summary,
        "suggested_filename": entry.suggested_filename,
        "target_path": storage_target_path(
            storage_root, target_path, entry.suggested_filename
        ),
        "citation_count": entry.upvotes,
        "influential_citation_count": 0,
        "huggingface_paper_url": entry.paper_url,
        "huggingface_submitted_at": entry.submitted_at,
        "huggingface_upvotes": entry.upvotes,
        "huggingface_comments": entry.num_comments,
        "github_stars": entry.github_stars,
        "github_repo": entry.github_repo,
        "project_page": entry.project_page,
        "organization": entry.organization,
        "ai_summary": entry.ai_summary,
        "ai_keywords": entry.ai_keywords,
        "source_metadata": {
            "huggingface_papers": {
                "paper_url": entry.paper_url,
                "submitted_at": entry.submitted_at,
                "upvotes": entry.upvotes,
                "comments": entry.num_comments,
                "github_stars": entry.github_stars,
                "github_repo": entry.github_repo,
                "project_page": entry.project_page,
                "organization": entry.organization,
                "ai_keywords": entry.ai_keywords,
            }
        },
    }
    if entry.paper_url:
        record["citation_source_url"] = entry.paper_url
    return normalize_record_identity(record, source="huggingface_papers")


def build_report(
    *,
    date: str | None,
    storage_root: str,
    category_name: str,
    target_path: str,
    limit: int | None = None,
    min_upvotes: int | None = None,
) -> dict:
    records = [
        paper_to_record(
            entry,
            storage_root=storage_root,
            target_path=target_path,
        )
        for entry in fetch_daily_papers(date)
    ]
    if min_upvotes is not None:
        records = [
            record
            for record in records
            if int(record.get("huggingface_upvotes") or 0) >= min_upvotes
        ]
    records.sort(
        key=lambda record: (
            int(record.get("huggingface_upvotes") or 0),
            int(record.get("github_stars") or 0),
            str(record.get("published") or ""),
        ),
        reverse=True,
    )
    if limit is not None:
        records = records[:limit]
    source_date = date or (
        records[0].get("huggingface_submitted_at", "")[:10] if records else None
    )
    return single_category_report(
        source="huggingface-papers",
        source_url=HF_PAPERS_URL,
        source_date=source_date,
        storage_root=storage_root,
        category_name=category_name,
        query=f"huggingface:daily_papers:{source_date or 'latest'}",
        target_path=target_path,
        recent=records,
        highly_cited=[],
        lookback_days=1,
    )
