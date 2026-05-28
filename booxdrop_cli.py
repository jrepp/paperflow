#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6.0.0",
#   "textual>=0.61.0",
#   "typer>=0.12.0",
#   "websockets>=15.0",
# ]
# ///

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import typer
import websockets
import yaml
import paperflow_radar as radar_core
from paperflow_sources_huggingface import build_report as build_huggingface_papers_report
from textual.app import App as TextualApp, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, ListItem, ListView, Static


DEFAULT_STORAGE_ROOT = "/storage/emulated/0/Books"
DEFAULT_SCAN_DIRS = [
    "/storage/emulated/0/Books",
    "/storage/emulated/0/Download",
]
ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_RADAR_CONFIG = "arxiv-radar.yaml"
DEFAULT_PDF_CACHE_DIR = "artifacts/pdf-cache"
DEFAULT_CURATED_MANIFEST_PATH = "artifacts/arxiv-radar-curated.json"
DEFAULT_MANIFEST_PATH = "artifacts/arxiv-radar-manifest.json"
DEFAULT_STAGED_MANIFEST_PATH = "artifacts/arxiv-radar-staged.json"
DEFAULT_REPORT_TEX_PATH = "artifacts/arxiv-radar-summary.tex"
DEFAULT_REPORT_PDF_PATH = "artifacts/arxiv-radar-summary.pdf"
DEFAULT_REPORT_BUILD_DIR = "artifacts/report-build"
DEFAULT_REPORT_SUMMARY_CACHE_DIR = "artifacts/summary-cache"
DEFAULT_REPORT_MARKDOWN_CACHE_DIR = "artifacts/markdown-cache"
DEFAULT_REPORT_MODEL = "openai/gpt-5.5"
DEFAULT_REPORT_PROMPT_VERSION = 3
ARXIV_ID_PATTERN = re.compile(
    r"(?P<id>(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?)$",
    re.IGNORECASE,
)
ARXIV_XML_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_META_NS = {"arxiv": "http://arxiv.org/schemas/atom"}


def basename(path: str) -> str:
    return PurePosixPath(path).name


def sanitize_filename(name: str) -> str:
    return radar_core.sanitize_filename(name)


def load_env_file(env_file: str) -> dict[str, str]:
    path = Path(env_file)
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def load_structured_file(file_path: str) -> dict:
    raw = yaml.safe_load(Path(file_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected structured object in {file_path}")
    return raw


def env_or_arg(
    args_value: str | None, env_values: dict[str, str], key: str
) -> str | None:
    return args_value or env_values.get(key) or os.environ.get(key)


def info(message: str) -> None:
    typer.secho(message, fg=typer.colors.BLUE)


def success(message: str) -> None:
    typer.secho(message, fg=typer.colors.GREEN)


def warning(message: str) -> None:
    typer.secho(message, fg=typer.colors.YELLOW)


def error(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)


@dataclass
class RuntimeInputs:
    host: str | None
    token: str | None
    password: str | None
    contract_path: str | None
    spec: PlanSpec | None


def resolve_runtime_inputs(
    env_file: str,
    host: str | None,
    token: str | None,
    password: str | None,
    contract: str | None,
    *,
    require_host: bool,
    require_contract: bool,
) -> RuntimeInputs:
    env_values = load_env_file(env_file)
    resolved_host = env_or_arg(host, env_values, "BOOXDROP_HOST")
    resolved_token = env_or_arg(token, env_values, "BOOXDROP_TOKEN")
    resolved_password = env_or_arg(password, env_values, "BOOXDROP_PASSWORD")
    resolved_contract = (
        contract
        or env_values.get("BOOXDROP_CONTRACT")
        or os.environ.get("BOOXDROP_CONTRACT")
        or env_values.get("BOOXDROP_PLAN")
        or os.environ.get("BOOXDROP_PLAN")
    )

    if require_host and not resolved_host:
        raise typer.BadParameter(
            "Missing BOOX Drop host. Pass --host or set BOOXDROP_HOST."
        )
    if require_contract and not resolved_contract:
        raise typer.BadParameter(
            "Missing sync contract path. Pass --contract or set BOOXDROP_CONTRACT."
        )

    spec: PlanSpec | None = None
    if resolved_contract:
        spec = load_contract(resolved_contract)

    return RuntimeInputs(
        host=resolved_host,
        token=resolved_token,
        password=resolved_password,
        contract_path=resolved_contract,
        spec=spec,
    )


@dataclass
class CategorySpec:
    name: str
    physical_targets: list[str]
    shelf_targets: list[str]


@dataclass
class PlanSpec:
    storage_root: str
    scan_dirs: list[str]
    categories: list[CategorySpec]

    @property
    def category_names(self) -> list[str]:
        return [category.name for category in self.categories]

    @property
    def physical_target_by_name(self) -> dict[str, str]:
        return {
            basename(path): path
            for category in self.categories
            for path in category.physical_targets
        }

    @property
    def shelf_target_by_id(self) -> dict[str, str]:
        return {
            item_id: category.name
            for category in self.categories
            for item_id in category.shelf_targets
        }


def contract_to_spec(raw: dict) -> PlanSpec:
    if isinstance(raw.get("sync_contract"), dict):
        raw = raw["sync_contract"]
    elif isinstance(raw.get("plan_fragment"), dict):
        raw = raw["plan_fragment"]

    storage_root = raw.get("storage_root") or DEFAULT_STORAGE_ROOT
    scan_dirs = raw.get("scan_dirs") or DEFAULT_SCAN_DIRS
    categories_raw = raw.get("categories") or {}

    if not isinstance(categories_raw, dict) or not categories_raw:
        raise ValueError("sync contract must define a non-empty 'categories' object")

    categories: list[CategorySpec] = []
    for name, category_raw in categories_raw.items():
        if not isinstance(category_raw, dict):
            raise ValueError(f"category '{name}' must be an object")
        physical_targets = list(category_raw.get("physical_targets") or [])
        shelf_targets = list(category_raw.get("shelf_targets") or physical_targets)
        categories.append(
            CategorySpec(
                name=name,
                physical_targets=physical_targets,
                shelf_targets=shelf_targets,
            )
        )

    plan = PlanSpec(
        storage_root=storage_root, scan_dirs=list(scan_dirs), categories=categories
    )
    duplicates = [
        file_name
        for file_name, count in Counter(plan.physical_target_by_name.keys()).items()
        if count > 1
    ]
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(
            f"sync contract has duplicate physical target basenames: {joined}"
        )
    return plan


def load_contract(contract_path: str) -> PlanSpec:
    return contract_to_spec(load_structured_file(contract_path))


@dataclass
class ArxivEntry:
    requested_id: str
    resolved_id: str
    title: str
    pdf_url: str
    abs_url: str
    summary: str
    suggested_filename: str
    target_path: str


@dataclass
class RadarCategorySpec:
    name: str
    query: str
    target_path: str


@dataclass
class RadarSpec:
    output_dir: str
    storage_root: str
    lookback_days: int
    recent_limit: int
    cited_limit: int
    citation_candidate_limit: int
    categories: list[RadarCategorySpec]
    export_section: str = "highly_cited"
    export_categories: list[str] = field(default_factory=list)
    export_exclude_categories: list[str] = field(default_factory=list)
    export_top: int | None = None
    export_min_citations: int | None = None
    export_max_citations: int | None = None
    export_since: str | None = None
    export_lookback_days: int | None = None
    report_model: str = DEFAULT_REPORT_MODEL
    report_variant: str | None = None
    report_title: str = "AI and Data Research Radar Summary"
    report_max_papers: int | None = None
    report_summary_cache_dir: str = DEFAULT_REPORT_SUMMARY_CACHE_DIR
    report_markdown_cache_dir: str = DEFAULT_REPORT_MARKDOWN_CACHE_DIR
    report_output_tex: str = DEFAULT_REPORT_TEX_PATH
    report_output_pdf: str = DEFAULT_REPORT_PDF_PATH
    report_build_dir: str = DEFAULT_REPORT_BUILD_DIR
    report_prompt_version: int = DEFAULT_REPORT_PROMPT_VERSION
    periodical_series: str = "Research Radar"
    periodical_issue: int | None = None
    periodical_focus: str | None = None
    periodical_supporting_papers: int = 6
    reference_depth: int = 2
    max_references_per_paper: int = 10
    min_reference_citations: int = 50


@dataclass
class SearchEntry:
    arxiv_id: str
    resolved_id: str
    title: str
    summary: str
    pdf_url: str
    abs_url: str
    authors: list[str]
    published: str
    updated: str
    primary_category: str
    suggested_filename: str


def http_get_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "arxiv-radar/0.1.0",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def http_get_xml(url: str, headers: dict[str, str] | None = None) -> ET.Element:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/atom+xml",
            "User-Agent": "arxiv-radar/0.1.0",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return ET.fromstring(response.read())


def parse_arxiv_search_entry(entry: ET.Element) -> SearchEntry:
    abs_url = entry.findtext("atom:id", default="", namespaces=ARXIV_XML_NS).strip()
    resolved_id = abs_url.rsplit("/", 1)[-1]
    pdf_url = ""
    for link in entry.findall("atom:link", ARXIV_XML_NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "").strip()
            break
    if pdf_url and not pdf_url.endswith(".pdf"):
        pdf_url = f"{pdf_url}.pdf"
    authors = [
        author.findtext("atom:name", default="", namespaces=ARXIV_XML_NS).strip()
        for author in entry.findall("atom:author", ARXIV_XML_NS)
    ]
    title = " ".join(
        entry.findtext("atom:title", default="", namespaces=ARXIV_XML_NS).split()
    )
    summary = " ".join(
        entry.findtext("atom:summary", default="", namespaces=ARXIV_XML_NS).split()
    )
    primary_category = ""
    primary_node = entry.find("arxiv:primary_category", ARXIV_META_NS)
    if primary_node is None:
        first_category = entry.find("atom:category", ARXIV_XML_NS)
        primary_category = (
            first_category.get("term", "") if first_category is not None else ""
        )
    else:
        primary_category = primary_node.get("term", "")
    return SearchEntry(
        arxiv_id=normalize_arxiv_id(resolved_id),
        resolved_id=resolved_id,
        title=title,
        summary=summary,
        pdf_url=pdf_url,
        abs_url=abs_url,
        authors=[author for author in authors if author],
        published=entry.findtext(
            "atom:published", default="", namespaces=ARXIV_XML_NS
        ).strip(),
        updated=entry.findtext(
            "atom:updated", default="", namespaces=ARXIV_XML_NS
        ).strip(),
        primary_category=primary_category,
        suggested_filename=f"{sanitize_filename(title)}.pdf",
    )


def fetch_arxiv_search(
    search_query: str,
    *,
    max_results: int,
    sort_by: str,
    sort_order: str,
) -> list[SearchEntry]:
    query = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
    )
    root = http_get_xml(f"{ARXIV_API_URL}?{query}")
    return [
        parse_arxiv_search_entry(entry)
        for entry in root.findall("atom:entry", ARXIV_XML_NS)
    ]


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_dateish_datetime(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value + "T00:00:00+00:00")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def citation_metadata(arxiv_id: str) -> dict:
    normalized = normalize_arxiv_id(arxiv_id)
    doi = f"10.48550/arXiv.{normalized}"
    url = f"https://api.openalex.org/works?filter=doi:{urllib.parse.quote(doi)}"
    try:
        payload = http_get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    results = payload.get("results") or []
    if not results:
        return {}
    top = results[0]
    return {
        "citationCount": int(top.get("cited_by_count") or 0),
        "influentialCitationCount": 0,
        "url": top.get("id"),
        "year": top.get("publication_year"),
    }


CITATION_CACHE_DIR = "artifacts/citation-cache"


def _citation_cache_path(arxiv_id: str) -> Path:
    return Path(CITATION_CACHE_DIR) / f"{slugify(normalize_arxiv_id(arxiv_id))}.json"


def _openalex_cache_path(openalex_id: str) -> Path:
    short_id = openalex_id.rsplit("/", 1)[-1]
    return Path(CITATION_CACHE_DIR) / f"oa-{short_id}.json"


def fetch_openalex_work(arxiv_id: str) -> dict:
    normalized = normalize_arxiv_id(arxiv_id)
    cache = _citation_cache_path(normalized)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    doi = f"10.48550/arXiv.{normalized}"
    url = f"https://api.openalex.org/works?filter=doi:{urllib.parse.quote(doi)}&select=id,doi,title,cited_by_count,publication_year,referenced_works,primary_location"
    try:
        payload = http_get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    results = payload.get("results") or []
    if not results:
        return {}
    work = results[0]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(work, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return work


def fetch_openalex_works_batch(openalex_ids: list[str]) -> list[dict]:
    if not openalex_ids:
        return []
    uncached = []
    cached = {}
    for oid in openalex_ids:
        cache = _openalex_cache_path(oid)
        if cache.exists():
            cached[oid] = json.loads(cache.read_text(encoding="utf-8"))
        else:
            uncached.append(oid)
    if uncached:
        filter_val = "|".join(uncached)
        url = f"https://api.openalex.org/works?filter=openalex:{urllib.parse.quote(filter_val)}&select=id,doi,title,cited_by_count,publication_year,referenced_works,primary_location&per_page=50"
        try:
            payload = http_get_json(url)
        except urllib.error.HTTPError:
            payload = {"results": []}
        for work in payload.get("results") or []:
            oid = work.get("id", "")
            cached[oid] = work
            cache = _openalex_cache_path(oid)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(work, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return [cached[oid] for oid in openalex_ids if oid in cached]


def _extract_arxiv_id_from_work(work: dict) -> str | None:
    loc = work.get("primary_location") or {}
    landing = loc.get("landing_page_url") or ""
    if "arxiv.org/abs/" in landing:
        return landing.split("arxiv.org/abs/")[-1].split("?")[0].split("v")[0]
    doi = work.get("doi") or ""
    if "10.48550/arxiv." in doi.lower():
        return doi.lower().split("arxiv.")[-1]
    return None


def _work_to_foundational_entry(work: dict, source_arxiv_id: str, depth: int) -> dict:
    arxiv_id = _extract_arxiv_id_from_work(work)
    title = work.get("title") or "Unknown"
    openalex_id = work.get("id", "")
    citations = int(work.get("cited_by_count") or 0)
    year = work.get("publication_year")
    doi = work.get("doi") or ""
    pdf_url = ""
    abs_url = ""
    if arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        abs_url = f"http://arxiv.org/abs/{arxiv_id}"
    elif doi:
        abs_url = f"https://doi.org/{doi.replace('https://doi.org/', '')}"
    return {
        "arxiv_id": arxiv_id or openalex_id.rsplit("/", 1)[-1],
        "title": title,
        "citation_count": citations,
        "publication_year": year,
        "doi": doi,
        "openalex_id": openalex_id,
        "pdf_url": pdf_url,
        "abs_url": abs_url,
        "source_arxiv_id": source_arxiv_id,
        "depth": depth,
        "has_pdf": bool(pdf_url),
        "category": "foundational",
        "section": "foundational",
        "authors": [],
        "summary": "",
        "published": f"{year}-01-01T00:00:00Z" if year else "",
        "updated": f"{year}-01-01T00:00:00Z" if year else "",
        "primary_category": "foundational",
    }


def traverse_references(
    entries: list[dict],
    *,
    max_depth: int = 2,
    max_refs_per_paper: int = 10,
    min_citations: int = 50,
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    all_discovered: dict[str, dict] = {}
    for entry in entries:
        arxiv_id = str(entry.get("resolved_id") or entry.get("arxiv_id", ""))
        if not arxiv_id:
            continue
        result[arxiv_id] = _traverse_refs_recursive(
            arxiv_id,
            max_depth=max_depth,
            max_refs=max_refs_per_paper,
            min_citations=min_citations,
            current_depth=1,
            all_discovered=all_discovered,
        )
    return result


def _traverse_refs_recursive(
    arxiv_id: str,
    *,
    max_depth: int,
    max_refs: int,
    min_citations: int,
    current_depth: int,
    all_discovered: dict[str, dict],
) -> list[dict]:
    if current_depth > max_depth:
        return []
    work = fetch_openalex_work(arxiv_id)
    ref_ids = work.get("referenced_works") or []
    if not ref_ids:
        return []
    ref_works = fetch_openalex_works_batch(ref_ids)
    scored = sorted(ref_works, key=lambda w: int(w.get("cited_by_count") or 0), reverse=True)
    filtered = [w for w in scored if int(w.get("cited_by_count") or 0) >= min_citations][:max_refs]
    entries = []
    for w in filtered:
        oid = w.get("id", "")
        if oid in all_discovered:
            continue
        entry = _work_to_foundational_entry(w, arxiv_id, current_depth)
        all_discovered[oid] = entry
        entries.append(entry)
        if current_depth < max_depth:
            child_id = _extract_arxiv_id_from_work(w)
            if child_id:
                children = _traverse_refs_recursive(
                    child_id,
                    max_depth=max_depth,
                    max_refs=max_refs,
                    min_citations=min_citations,
                    current_depth=current_depth + 1,
                    all_discovered=all_discovered,
                )
                entries.extend(children)
    return entries


def load_radar_config(config_path: str) -> RadarSpec:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    categories_raw = raw.get("categories") or {}
    if not isinstance(categories_raw, dict) or not categories_raw:
        raise ValueError("radar config must define a non-empty 'categories' object")

    categories: list[RadarCategorySpec] = []
    for name, category_raw in categories_raw.items():
        if not isinstance(category_raw, dict):
            raise ValueError(f"radar category '{name}' must be an object")
        query = category_raw.get("query")
        target_path = category_raw.get("target_path")
        if not query or not target_path:
            raise ValueError(f"radar category '{name}' needs 'query' and 'target_path'")
        categories.append(
            RadarCategorySpec(name=name, query=query, target_path=target_path)
        )

    export_raw = raw.get("export") or {}
    if not isinstance(export_raw, dict):
        raise ValueError("radar config 'export' must be an object if present")

    report_raw = raw.get("report") or {}
    if not isinstance(report_raw, dict):
        raise ValueError("radar config 'report' must be an object if present")
    periodical_raw = raw.get("periodical") or {}
    if not isinstance(periodical_raw, dict):
        raise ValueError("radar config 'periodical' must be an object if present")

    return RadarSpec(
        output_dir=raw.get("output_dir") or "radar-output",
        storage_root=raw.get("storage_root") or DEFAULT_STORAGE_ROOT,
        lookback_days=int(raw.get("lookback_days") or 7),
        recent_limit=int(raw.get("recent_limit") or 10),
        cited_limit=int(raw.get("cited_limit") or 10),
        citation_candidate_limit=int(raw.get("citation_candidate_limit") or 30),
        categories=categories,
        export_section=str(export_raw.get("section") or "highly_cited"),
        export_categories=[str(item) for item in (export_raw.get("categories") or [])],
        export_exclude_categories=[
            str(item) for item in (export_raw.get("exclude_categories") or [])
        ],
        export_top=(
            int(export_raw["top"]) if export_raw.get("top") is not None else None
        ),
        export_min_citations=(
            int(export_raw["min_citations"])
            if export_raw.get("min_citations") is not None
            else None
        ),
        export_max_citations=(
            int(export_raw["max_citations"])
            if export_raw.get("max_citations") is not None
            else None
        ),
        export_since=(
            str(export_raw["since"]) if export_raw.get("since") is not None else None
        ),
        export_lookback_days=(
            int(export_raw["lookback_days"])
            if export_raw.get("lookback_days") is not None
            else None
        ),
        report_model=str(report_raw.get("model") or DEFAULT_REPORT_MODEL),
        report_variant=(
            str(report_raw["variant"])
            if report_raw.get("variant") is not None
            else None
        ),
        report_title=str(
            report_raw.get("title") or "AI and Data Research Radar Summary"
        ),
        report_max_papers=(
            int(report_raw["max_papers"])
            if report_raw.get("max_papers") is not None
            else None
        ),
        report_summary_cache_dir=str(
            report_raw.get("summary_cache_dir") or DEFAULT_REPORT_SUMMARY_CACHE_DIR
        ),
        report_markdown_cache_dir=str(
            report_raw.get("markdown_cache_dir") or DEFAULT_REPORT_MARKDOWN_CACHE_DIR
        ),
        report_output_tex=str(report_raw.get("output_tex") or DEFAULT_REPORT_TEX_PATH),
        report_output_pdf=str(report_raw.get("output_pdf") or DEFAULT_REPORT_PDF_PATH),
        report_build_dir=str(report_raw.get("build_dir") or DEFAULT_REPORT_BUILD_DIR),
        report_prompt_version=int(
            report_raw.get("prompt_version") or DEFAULT_REPORT_PROMPT_VERSION
        ),
        periodical_series=str(periodical_raw.get("series") or "Research Radar"),
        periodical_issue=(
            int(periodical_raw["issue"])
            if periodical_raw.get("issue") is not None
            else None
        ),
        periodical_focus=(
            str(periodical_raw["focus"])
            if periodical_raw.get("focus") is not None
            else None
        ),
        periodical_supporting_papers=int(
            periodical_raw.get("supporting_papers") or 6
        ),
        reference_depth=int(raw.get("reference_depth") or 2),
        max_references_per_paper=int(raw.get("max_references_per_paper") or 10),
        min_reference_citations=int(raw.get("min_reference_citations") or 50),
    )


def entry_target_path(
    storage_root: str, relative_target_path: str, entry: SearchEntry
) -> str:
    return str(
        PurePosixPath(storage_root) / relative_target_path / entry.suggested_filename
    )


def build_radar_category_report(spec: RadarSpec, category: RadarCategorySpec) -> dict:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=spec.lookback_days)
    recent_candidates = fetch_arxiv_search(
        category.query,
        max_results=max(spec.recent_limit * 5, 25),
        sort_by="submittedDate",
        sort_order="descending",
    )
    recent_entries = [
        entry
        for entry in recent_candidates
        if parse_iso_datetime(entry.published) >= cutoff
    ][: spec.recent_limit]

    cited_candidates = fetch_arxiv_search(
        category.query,
        max_results=spec.citation_candidate_limit,
        sort_by="relevance",
        sort_order="descending",
    )

    citation_cache: dict[str, dict] = {}

    def get_citation_meta(arxiv_id: str) -> dict:
        normalized = normalize_arxiv_id(arxiv_id)
        if normalized not in citation_cache:
            citation_cache[normalized] = citation_metadata(normalized)
        return citation_cache[normalized]

    recent_ids = {entry.arxiv_id for entry in recent_entries}
    cited_ranked = []
    for entry in cited_candidates:
        meta = get_citation_meta(entry.arxiv_id)
        cited_ranked.append(
            {
                "entry": entry,
                "citation_count": int(meta.get("citationCount") or 0),
                "influential_citation_count": int(
                    meta.get("influentialCitationCount") or 0
                ),
                "citation_source_url": meta.get("url"),
                "citation_source_year": meta.get("year"),
            }
        )
    cited_ranked.sort(
        key=lambda item: (
            item["citation_count"],
            item["influential_citation_count"],
            item["entry"].published,
        ),
        reverse=True,
    )

    cited_entries = []
    for item in cited_ranked:
        entry = item["entry"]
        if entry.arxiv_id in recent_ids:
            continue
        cited_entries.append(item)
        if len(cited_entries) >= spec.cited_limit:
            break

    recent_ranked = []
    for entry in recent_entries:
        meta = get_citation_meta(entry.arxiv_id)
        recent_ranked.append(
            {
                "entry": entry,
                "citation_count": int(meta.get("citationCount") or 0),
                "influential_citation_count": int(
                    meta.get("influentialCitationCount") or 0
                ),
                "citation_source_url": meta.get("url"),
                "citation_source_year": meta.get("year"),
            }
        )

    def to_record(
        entry: SearchEntry,
        *,
        citation_count: int | None = None,
        influential_citation_count: int | None = None,
        citation_source_url: str | None = None,
    ) -> dict:
        record = {
            "arxiv_id": entry.arxiv_id,
            "resolved_id": entry.resolved_id,
            "title": entry.title,
            "authors": entry.authors,
            "published": entry.published,
            "updated": entry.updated,
            "primary_category": entry.primary_category,
            "abs_url": entry.abs_url,
            "pdf_url": entry.pdf_url,
            "summary": entry.summary,
            "suggested_filename": entry.suggested_filename,
            "target_path": entry_target_path(
                spec.storage_root, category.target_path, entry
            ),
        }
        if citation_count is not None:
            record["citation_count"] = citation_count
        if influential_citation_count is not None:
            record["influential_citation_count"] = influential_citation_count
        if citation_source_url:
            record["citation_source_url"] = citation_source_url
        return record

    return {
        "name": category.name,
        "query": category.query,
        "target_path": category.target_path,
        "recent": [
            to_record(
                item["entry"],
                citation_count=item["citation_count"],
                influential_citation_count=item["influential_citation_count"],
                citation_source_url=item["citation_source_url"],
            )
            for item in recent_ranked
        ],
        "highly_cited": [
            to_record(
                item["entry"],
                citation_count=item["citation_count"],
                influential_citation_count=item["influential_citation_count"],
                citation_source_url=item["citation_source_url"],
            )
            for item in cited_entries
        ],
    }


def radar_markdown(report: dict) -> str:
    return radar_core.radar_markdown(report)


def write_radar_outputs(report: dict, output_dir: str) -> tuple[Path, Path]:
    return radar_core.write_radar_outputs(report, output_dir, prefix="arxiv-radar")


def write_huggingface_papers_radar_outputs(
    report: dict, output_dir: str
) -> tuple[Path, Path]:
    return radar_core.write_radar_outputs(report, output_dir, prefix="hf-papers-radar")


def load_radar_report(report_path: str) -> dict:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def latest_radar_report_path(output_dir: str) -> Path:
    return radar_core.latest_report_path(output_dir, prefix="arxiv-radar")


def latest_huggingface_papers_report_path(output_dir: str) -> Path:
    return radar_core.latest_report_path(output_dir, prefix="hf-papers-radar")


def default_curated_output_path(report_path: Path) -> Path:
    return Path(DEFAULT_CURATED_MANIFEST_PATH)


def section_output_suffix(section: str) -> str:
    return section.replace("_", "-")


def default_export_output_path(report_path: Path, section: str) -> Path:
    return Path(DEFAULT_MANIFEST_PATH)


def default_staged_output_path(manifest_path: Path) -> Path:
    return Path(DEFAULT_STAGED_MANIFEST_PATH)


def flatten_radar_report(report: dict) -> list[dict]:
    items: list[dict] = []
    for category in report.get("categories", []):
        for section in ("recent", "highly_cited"):
            for entry in category.get(section, []):
                items.append(
                    {
                        "key": f"{category['name']}::{section}::{entry['arxiv_id']}",
                        "category": category["name"],
                        "section": section,
                        "entry": entry,
                    }
                )
    return items


def curated_manifest(
    report: dict, report_path: Path, selected_items: list[dict]
) -> dict:
    categories: dict[str, dict[str, list[str]]] = {}
    entries: list[dict] = []
    for item in selected_items:
        category_name = item["category"]
        entry = item["entry"]
        entries.append(
            {
                "category": category_name,
                "section": item["section"],
                **entry,
            }
        )
        category = categories.setdefault(
            category_name,
            {"physical_targets": [], "shelf_targets": []},
        )
        category["physical_targets"].append(entry["target_path"])
        category["shelf_targets"].append(entry["target_path"])

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "arxiv-radar-curation",
        "source_report": str(report_path),
        "storage_root": report["storage_root"],
        "selected_count": len(entries),
        "entries": entries,
        "sync_contract": {
            "kind": "library_sync_contract",
            "version": 1,
            "storage_root": report["storage_root"],
            "categories": categories,
        },
    }


def filter_radar_items(
    items: list[dict],
    *,
    section: str,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    top: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since: str | None = None,
    lookback_days: int | None = None,
    reference_time: datetime | None = None,
) -> list[dict]:
    if section not in {"recent", "highly_cited", "all"}:
        raise ValueError("section must be one of: recent, highly_cited, all")

    filtered = (
        list(items)
        if section == "all"
        else [item for item in items if item["section"] == section]
    )

    if categories:
        allowed = {category.casefold() for category in categories}
        filtered = [item for item in filtered if item["category"].casefold() in allowed]

    if exclude_categories:
        blocked = {category.casefold() for category in exclude_categories}
        filtered = [
            item for item in filtered if item["category"].casefold() not in blocked
        ]

    if min_citations is not None:
        if min_citations < 0:
            raise ValueError("min_citations must be zero or greater")
        filtered = [
            item
            for item in filtered
            if int(item["entry"].get("citation_count") or 0) >= min_citations
        ]

    if max_citations is not None:
        if max_citations < 0:
            raise ValueError("max_citations must be zero or greater")
        filtered = [
            item
            for item in filtered
            if int(item["entry"].get("citation_count") or 0) <= max_citations
        ]

    cutoff: datetime | None = None
    if since:
        cutoff = parse_dateish_datetime(since)
    elif lookback_days is not None:
        if lookback_days < 0:
            raise ValueError("lookback_days must be zero or greater")
        cutoff = (reference_time or datetime.now(UTC)) - timedelta(days=lookback_days)

    if cutoff is not None:
        filtered = [
            item
            for item in filtered
            if parse_iso_datetime(
                item["entry"].get("published", "1970-01-01T00:00:00Z")
            )
            >= cutoff
        ]

    if top is not None:
        if top <= 0:
            raise ValueError("top must be greater than zero")
        filtered = sorted(
            filtered,
            key=lambda item: (
                int(item["entry"].get("citation_count") or 0),
                item["entry"].get("published", ""),
                item["entry"].get("title", ""),
            ),
            reverse=True,
        )[:top]

    return filtered


def export_radar_manifest(
    report_path: str,
    *,
    section: str,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    top: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since: str | None = None,
    lookback_days: int | None = None,
    output_path: str | None = None,
    db_path: str | None = None,
) -> Path:
    report_file = Path(report_path)
    report = load_radar_report(str(report_file))
    items = filter_radar_items(
        flatten_radar_report(report),
        section=section,
        categories=categories,
        exclude_categories=exclude_categories,
        top=top,
        min_citations=min_citations,
        max_citations=max_citations,
        since=since,
        lookback_days=lookback_days,
        reference_time=parse_dateish_datetime(
            str(report.get("generated_at") or datetime.now(UTC).isoformat())
        ),
    )
    manifest = curated_manifest(report, report_file, items)
    target = (
        Path(output_path)
        if output_path
        else default_export_output_path(report_file, section)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if db_path:
        _record_export_to_db(db_path, manifest, section=section, categories=categories, top=top, min_citations=min_citations, max_citations=max_citations, since=since, lookback_days=lookback_days)

    return target


def radar_export_summary(
    report_path: str,
    *,
    section: str,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    top: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since: str | None = None,
    lookback_days: int | None = None,
) -> dict:
    report = load_radar_report(report_path)
    items = filter_radar_items(
        flatten_radar_report(report),
        section=section,
        categories=categories,
        exclude_categories=exclude_categories,
        top=top,
        min_citations=min_citations,
        max_citations=max_citations,
        since=since,
        lookback_days=lookback_days,
        reference_time=parse_dateish_datetime(
            str(report.get("generated_at") or datetime.now(UTC).isoformat())
        ),
    )
    return {
        "selected_count": len(items),
        "categories": sorted({item["category"] for item in items}),
        "sections": sorted({item["section"] for item in items}),
    }


def summarize_manifest(manifest: dict) -> dict:
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    categories: dict[str, int] = {}
    sections: dict[str, int] = {}
    with_pdf_url = 0
    with_target_path = 0
    for entry in entries:
        category = str(entry.get("category") or "unknown")
        section = str(entry.get("section") or "unknown")
        categories[category] = categories.get(category, 0) + 1
        sections[section] = sections.get(section, 0) + 1
        if entry.get("pdf_url"):
            with_pdf_url += 1
        if entry.get("target_path"):
            with_target_path += 1
    return {
        "selected_count": manifest.get("selected_count", len(entries)),
        "entry_count": len(entries),
        "categories": categories,
        "sections": sections,
        "with_pdf_url": with_pdf_url,
        "with_target_path": with_target_path,
        "storage_root": manifest.get("storage_root")
        or manifest.get("sync_contract", {}).get("storage_root"),
    }


def summarize_cache(manifest: dict, cache_dir: str = DEFAULT_PDF_CACHE_DIR) -> dict:
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    cached = []
    missing = []
    total_bytes = 0
    for entry in entries:
        if not entry.get("pdf_url"):
            continue
        cache_path = cache_file_path(cache_dir, entry)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            cached.append(
                {
                    "entry": entry,
                    "path": str(cache_path),
                    "size": cache_path.stat().st_size,
                }
            )
            total_bytes += cache_path.stat().st_size
        else:
            missing.append({"entry": entry, "path": str(cache_path)})
    return {
        "cache_dir": cache_dir,
        "manifest_entries": len(entries),
        "cacheable_entries": len(cached) + len(missing),
        "cached_entries": len(cached),
        "missing_entries": len(missing),
        "total_bytes": total_bytes,
        "cached": cached,
        "missing": missing,
    }


def staged_manifest(manifest: dict, manifest_path: str, cache_dir: str) -> dict:
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    staged_entries = []
    for entry in entries:
        staged_entry = dict(entry)
        if entry.get("pdf_url"):
            staged_entry["local_pdf_path"] = str(cache_file_path(cache_dir, entry))
        staged_entries.append(staged_entry)
    staged = dict(manifest)
    staged["source"] = "arxiv-radar-stage"
    staged["source_manifest"] = manifest_path
    staged["entries"] = staged_entries
    return staged


async def prime_cache(
    manifest_path: str, cache_dir: str = DEFAULT_PDF_CACHE_DIR
) -> dict:
    manifest = load_manifest(manifest_path)
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    reused = 0
    for entry in entries:
        if not entry.get("pdf_url"):
            continue
        cache_path = cache_file_path(cache_dir, entry)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            reused += 1
            continue
        content = await download_binary(entry["pdf_url"])
        cache_path.write_bytes(content)
        downloaded += 1
    summary = summarize_cache(manifest, cache_dir)
    summary["downloaded_entries"] = downloaded
    summary["reused_entries"] = reused
    return summary


async def stage_manifest(
    manifest_path: str,
    cache_dir: str = DEFAULT_PDF_CACHE_DIR,
    output_path: str | None = None,
) -> Path:
    await prime_cache(manifest_path, cache_dir)
    manifest = load_manifest(manifest_path)
    staged = staged_manifest(manifest, manifest_path, cache_dir)
    target = (
        Path(output_path)
        if output_path
        else default_staged_output_path(Path(manifest_path))
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(staged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


async def prepare_manifest(
    report_path: str,
    *,
    section: str,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    top: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since: str | None = None,
    lookback_days: int | None = None,
    manifest_output: str | None = None,
    cache_dir: str = DEFAULT_PDF_CACHE_DIR,
    staged_output: str | None = None,
    db_path: str | None = None,
    curated_path: str | None = None,
) -> dict:
    if curated_path and Path(curated_path).exists():
        curated = Path(curated_path)
        target = Path(manifest_output) if manifest_output else Path(DEFAULT_MANIFEST_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(curated, target)
        manifest_path = target
    else:
        manifest_path = export_radar_manifest(
            report_path,
            section=section,
            categories=categories,
            exclude_categories=exclude_categories,
            top=top,
            min_citations=min_citations,
            max_citations=max_citations,
            since=since,
            lookback_days=lookback_days,
            output_path=manifest_output,
            db_path=db_path,
        )
    cache_summary = await prime_cache(str(manifest_path), cache_dir)
    staged_path = await stage_manifest(str(manifest_path), cache_dir, staged_output)
    return {
        "manifest_path": str(manifest_path),
        "staged_path": str(staged_path),
        "cache_summary": cache_summary,
        "manifest_summary": summarize_manifest(load_manifest(str(manifest_path))),
    }


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "default"


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def latex_table_cell(value: str, limit: int = 56) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) > limit:
        compact = compact[: limit - 3].rstrip() + "..."
    return latex_escape(compact)


def confidence_badge(value: str) -> str:
    normalized = str(value or "unknown").strip().upper()
    return rf"\textbf{{[{latex_escape(normalized)}]}}"


def summary_cache_path(
    summary_cache_dir: str,
    entry: dict,
    *,
    model: str,
    variant: str | None,
    prompt_version: int,
) -> Path:
    entry_id = str(entry.get("resolved_id") or entry.get("arxiv_id") or "unknown")
    model_slug = slugify(model)
    variant_slug = slugify(variant or "default")
    return (
        Path(summary_cache_dir)
        / f"{slugify(entry_id)}--{model_slug}--{variant_slug}--v{prompt_version}.json"
    )


def summary_prompt_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".prompt.txt")


def summary_meta_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".meta.json")


def summary_index_path(summary_cache_dir: str) -> Path:
    return Path(summary_cache_dir) / "index.json"


def markdown_cache_path(markdown_cache_dir: str, entry: dict) -> Path:
    entry_id = str(entry.get("resolved_id") or entry.get("arxiv_id") or "unknown")
    return Path(markdown_cache_dir) / f"{slugify(entry_id)}.md"


def clean_pdf_markdown(text: str) -> str:
    text = re.sub(r"\n*©.*?\n*", "\n", text)
    text = re.sub(r"\n\s*\*\*\d+\*\*\s*\n", "\n", text)
    text = re.sub(r"\n\s{2,}[-•]\s", "\n- ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"([a-z,])\n([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])\n(\([A-Z])", r"\1 \2", text)
    text = re.sub(r"(\d)\)\n([a-z])", r"\1) \2", text)
    lines = text.split("\n")
    in_code_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
            else:
                fence_len = 3
                while len(stripped) > fence_len and stripped[fence_len] == "`":
                    fence_len += 1
                rest = stripped[fence_len:]
                if not rest or rest.isspace():
                    lines[i] = stripped[:fence_len] + "text"
                in_code_block = True
    text = "\n".join(lines)
    text = re.sub(r"-_ ", "-* ", text)
    text = re.sub(r"-_$", "-*", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(\)", r"\1", text)
    text = re.sub(r"^(#{1,6})([A-Za-z])", r"\1 \2", text, flags=re.MULTILINE)
    text = re.sub(r"`\s*,\s+", "`, ", text)
    text = _normalize_heading_levels(text)
    text = _ensure_blank_lines(text)
    return text.strip()


def _normalize_heading_levels(text: str) -> str:
    lines = text.split("\n")
    result = []
    current_level = 0
    for line in lines:
        match = re.match(r"^(#{1,6})\s", line)
        if match:
            actual_level = len(match.group(1))
            if current_level == 0:
                current_level = actual_level
            else:
                if actual_level > current_level + 1:
                    new_level = current_level + 1
                    line = "#" * new_level + line[actual_level:]
                    current_level = new_level
                elif actual_level <= current_level:
                    current_level = actual_level
                else:
                    current_level = actual_level
        result.append(line)
    return "\n".join(result)


def _ensure_blank_lines(text: str) -> str:
    lines = text.split("\n")
    result = []
    prev_type = "text"
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        curr_type = "text"
        if not stripped:
            curr_type = "blank"
        elif stripped.startswith("```"):
            curr_type = "code_fence"
            in_code_block = not in_code_block
        elif in_code_block:
            curr_type = "code"
        elif re.match(r"^#{1,6}\s", stripped):
            curr_type = "heading"
        elif re.match(r"^[-*+]\s", stripped) or re.match(r"^\d+\.\s", stripped):
            curr_type = "list"
        if curr_type in ("heading", "code_fence") and prev_type not in (
            "blank",
            "code_fence",
        ):
            result.append("")
        elif curr_type == "list" and prev_type not in ("blank", "list"):
            result.append("")
        result.append(line)
        prev_type = curr_type
    cleaned = []
    prev_blank = False
    for line in cleaned if False else result:
        if not line.strip():
            if not prev_blank:
                cleaned.append(line)
            prev_blank = True
        else:
            cleaned.append(line)
            prev_blank = False
    return "\n".join(cleaned)


def write_summary_cache_record(
    *,
    cache_path: Path,
    entry: dict,
    prompt: str,
    files: list[str],
    summary: dict,
    model: str,
    variant: str | None,
    prompt_version: int,
    summary_cache_dir: str,
) -> None:
    prompt_path = summary_prompt_path(cache_path)
    meta_path = summary_meta_path(cache_path)
    prompt_path.write_text(prompt, encoding="utf-8")
    cache_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    meta = {
        "entry_id": entry.get("resolved_id") or entry.get("arxiv_id"),
        "title": entry.get("title"),
        "category": entry.get("category"),
        "model": model,
        "variant": variant,
        "prompt_version": prompt_version,
        "prompt_path": str(prompt_path),
        "summary_path": str(cache_path),
        "attached_files": files,
        "prompt_sha256": prompt_hash,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    index_path = summary_index_path(summary_cache_dir)
    index: dict = {"updated_at": datetime.now(UTC).isoformat(), "entries": {}}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                index = loaded
                index.setdefault("entries", {})
        except json.JSONDecodeError:
            pass
    entry_id = str(entry.get("resolved_id") or entry.get("arxiv_id") or cache_path.stem)
    index["updated_at"] = datetime.now(UTC).isoformat()
    index["entries"][entry_id] = meta
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


async def extract_markdown_from_pdf(
    entry: dict,
    *,
    cache_dir: str,
    markdown_cache_dir: str,
    db_path: str | None = None,
) -> Path:
    arxiv_id = str(entry.get("arxiv_id", ""))
    resolved_id = str(entry.get("resolved_id", ""))
    pdf_path = Path(entry.get("local_pdf_path") or cache_file_path(cache_dir, entry))
    if not pdf_path.exists():
        raise FileNotFoundError(f"pdf not found for markdown extraction: {pdf_path}")
    output_path = markdown_cache_path(markdown_cache_dir, entry)

    if db_path and arxiv_id:
        from radar_db import get_db as _gdb, get_extraction as _ge, upsert_extraction as _ue, ExtractionRecord as _ER
        with _gdb(db_path) as _conn:
            existing = _ge(_conn, arxiv_id, resolved_id=resolved_id)
        if existing and existing["status"] == "completed":
            recorded_path = existing.get("output_path", "")
            if recorded_path and Path(recorded_path).exists():
                return Path(recorded_path)

    if output_path.exists() and output_path.stat().st_size > 0:
        if db_path and arxiv_id:
            from radar_db import get_db as _gdb2, upsert_extraction as _ue2, ExtractionRecord as _ER2
            with _gdb2(db_path) as _conn:
                _ue2(_conn, _ER2(
                    arxiv_id=arxiv_id,
                    resolved_id=resolved_id,
                    status="completed",
                    input_path=str(pdf_path),
                    output_path=str(output_path),
                    extractor="pymupdf4llm",
                    char_count=output_path.stat().st_size,
                    completed_at=datetime.now(UTC).isoformat(),
                ))
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _extract() -> str:
        import pymupdf4llm

        return pymupdf4llm.to_markdown(
            str(pdf_path),
            ignore_images=True,
            ignore_graphics=True,
            table_strategy="lines_strict",
            margins=40,
            fontsize_limit=5,
        )

    markdown = await asyncio.to_thread(_extract)
    output_path.write_text(clean_pdf_markdown(markdown) + "\n", encoding="utf-8")

    if db_path and arxiv_id:
        from radar_db import get_db as _gdb3, upsert_extraction as _ue3, ExtractionRecord as _ER3
        with _gdb3(db_path) as _conn:
            _ue3(_conn, _ER3(
                arxiv_id=arxiv_id,
                resolved_id=resolved_id,
                status="completed",
                input_path=str(pdf_path),
                output_path=str(output_path),
                extractor="pymupdf4llm",
                char_count=len(markdown),
                completed_at=datetime.now(UTC).isoformat(),
            ))

    return output_path


def strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _json_candidate(text: str) -> str:
    payload = strip_json_fence(text)
    start = payload.find("{")
    end = payload.rfind("}")
    if start >= 0 and end > start:
        return payload[start : end + 1]
    return payload


def _repair_json_escapes(text: str) -> str:
    # Models sometimes emit LaTeX-like backslashes in JSON strings. Preserve valid
    # JSON escapes and double any other bare backslash so the payload can parse.
    return re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r"\\\\", text)


def parse_model_json(text: str) -> dict:
    payload = _json_candidate(text)
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        result = json.loads(_repair_json_escapes(payload))
    if not isinstance(result, dict):
        raise ValueError("opencode summary response was not a JSON object")
    return result


async def run_opencode_json(
    prompt: str,
    *,
    model: str,
    variant: str | None = None,
    files: list[str] | None = None,
) -> dict:
    args = ["opencode", "run", "--format", "json", "--pure", "-m", model]
    if variant:
        args.extend(["--variant", variant])
    for file_path in files or []:
        args.extend(["-f", file_path])
    args.append("--")
    args.append(prompt)

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd="/Users/jrepp/dev/boox-org",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"opencode run failed with {process.returncode}: {stderr.decode('utf-8', errors='replace')}"
        )

    texts: list[str] = []
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        event = json.loads(line)
        if event.get("type") == "text":
            texts.append(str(event.get("part", {}).get("text", "")))

    payload = "".join(texts)
    try:
        return parse_model_json(payload)
    except json.JSONDecodeError:
        retry_prompt = (
            "The previous response could not be parsed as valid JSON. "
            "Return only one valid JSON object, with all string newlines and backslashes escaped correctly. "
            "Keep every string concise: at most 2 sentences for scalar fields and at most 3 array items. "
            "Do not include markdown, explanation, or code fences.\n\n"
            f"Original task:\n{prompt}"
        )
        retry_args = ["opencode", "run", "--format", "json", "--pure", "-m", model]
        if variant:
            retry_args.extend(["--variant", variant])
        for file_path in files or []:
            retry_args.extend(["-f", file_path])
        retry_args.extend(["--", retry_prompt])
        retry_process = await asyncio.create_subprocess_exec(
            *retry_args,
            cwd="/Users/jrepp/dev/boox-org",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        retry_stdout, retry_stderr = await retry_process.communicate()
        if retry_process.returncode != 0:
            raise RuntimeError(
                f"opencode run failed with {retry_process.returncode}: {retry_stderr.decode('utf-8', errors='replace')}"
            )
        retry_texts: list[str] = []
        for raw_line in retry_stdout.decode("utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "text":
                retry_texts.append(str(event.get("part", {}).get("text", "")))
        return parse_model_json("".join(retry_texts))


def report_prompt(entry: dict, citation_count: int) -> str:
    metadata = {
        "title": entry.get("title"),
        "arxiv_id": entry.get("arxiv_id"),
        "category": entry.get("category"),
        "published": entry.get("published"),
        "citation_count": citation_count,
        "abstract": entry.get("summary"),
    }
    schema = {
        "results_summary": "2-4 sentence summary of the paper's result and approach",
        "intake_framing": "2-4 sentence framing for why this matters and how to read it",
        "area_of_progress": "short phrase describing what frontier or capability the paper pushes on",
        "citation_signal": "1-2 sentence explanation of what the incoming citation count suggests",
        "source_basis": "one of: pdf_and_metadata, metadata_only, mostly_metadata",
        "confidence": "one of: high, medium, low",
        "confidence_rationale": "1-2 sentence explanation of the confidence level and evidence basis",
        "primary_claims": ["claim 1", "claim 2", "claim 3"],
        "required_background": ["background area 1", "background area 2"],
        "background_research": [
            "follow-up concept or paper 1",
            "follow-up concept or paper 2",
        ],
        "open_questions": ["open question 1", "open question 2"],
    }
    return (
        "You are preparing an intake summary for a research radar PDF. "
        "Use the attached paper PDF as the primary source when available, and use the metadata below as supporting context. "
        "Return only valid JSON matching this schema and do not wrap it in markdown fences.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Paper metadata:\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n"
    )


def foundational_report_prompt(entry: dict, citation_count: int) -> str:
    metadata = {
        "title": entry.get("title"),
        "arxiv_id": entry.get("arxiv_id"),
        "openalex_id": entry.get("openalex_id"),
        "category": entry.get("category"),
        "published": entry.get("published"),
        "publication_year": entry.get("publication_year"),
        "citation_count": citation_count,
        "abstract": entry.get("summary"),
        "source_arxiv_id": entry.get("source_arxiv_id"),
        "reference_depth": entry.get("reference_depth"),
    }
    schema = {
        "results_summary": "5-8 sentence summary of the paper's core contribution, mechanism, evidence, and limits",
        "intake_framing": "4-6 sentence explanation of why this is foundational for the current radar issue and how to read it before or alongside newer papers",
        "area_of_progress": "short phrase naming the research lineage, method family, or capability frontier this paper anchors",
        "citation_signal": "2-3 sentence interpretation of citation count, age, and why the paper remains relevant or has been superseded",
        "source_basis": "one of: pdf_and_metadata, metadata_only, mostly_metadata",
        "confidence": "one of: high, medium, low",
        "confidence_rationale": "2-3 sentence explanation of the confidence level, source evidence, and any missing context",
        "primary_claims": [
            "precise claim about the paper's durable technical contribution",
            "claim about assumptions, scope, or empirical basis",
            "claim about influence on later work",
        ],
        "required_background": [
            "specific concept needed to read the paper well",
            "specific prerequisite method, benchmark, or theory",
        ],
        "background_research": [
            "paper, concept, or lineage to study before/after this foundation",
            "follow-on area that connects this foundation to current radar papers",
        ],
        "open_questions": [
            "long-horizon research question exposed by this paper",
            "modern reinterpretation or unresolved limitation",
        ],
        "long_range_insights": [
            "strategic implication for where this research direction may matter over the next several years",
            "connection between this foundation and emerging AI/data systems capabilities",
            "risk, bottleneck, or opportunity suggested by the paper's intellectual trajectory",
        ],
        "modern_connections": [
            "how this paper connects to current model, data, retrieval, evaluation, or systems work",
            "what newer papers inherit, challenge, or operationalize from it",
        ],
    }
    return (
        "You are re-evaluating a foundational reference for a research radar periodical using GPT-5.5. "
        "Prioritize deep synthesis over a short abstract: identify the paper's durable idea, why it became reusable, "
        "what later work likely inherited from it, and where its assumptions may now be obsolete. "
        "Use the attached paper PDF or extracted markdown as the primary source when available, and use the metadata below as supporting context. "
        "Write insightfully for an expert reader deciding how this foundation changes their reading of the current radar issue. "
        "Return only valid JSON matching this schema and do not wrap it in markdown fences.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Paper metadata:\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n"
    )


def category_report_prompt(category: str, items: list[dict]) -> str:
    payload = []
    for item in items:
        entry = item["entry"]
        summary = item["summary"]
        payload.append(
            {
                "title": entry.get("title"),
                "arxiv_id": entry.get("arxiv_id"),
                "citation_count": entry.get("citation_count"),
                "area_of_progress": summary.get("area_of_progress"),
                "results_summary": summary.get("results_summary"),
                "primary_claims": summary.get("primary_claims"),
                "required_background": summary.get("required_background"),
            }
        )
    schema = {
        "executive_summary": "3-5 sentence category-level overview of the current papers",
        "shared_themes": ["theme 1", "theme 2", "theme 3"],
        "intake_priorities": [
            "what to read first and why",
            "what background to build first",
        ],
    }
    return (
        "You are preparing a category-level executive summary for a research radar report. "
        "Synthesize the paper-level notes below and return only valid JSON matching this schema.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Category: {category}\n"
        f"Paper summaries:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def category_summary_cache_path(
    summary_cache_dir: str,
    category: str,
    *,
    model: str,
    variant: str | None,
    prompt_version: int,
) -> Path:
    return (
        Path(summary_cache_dir)
        / f"category--{slugify(category)}--{slugify(model)}--{slugify(variant or 'default')}--v{prompt_version}.json"
    )


async def summarize_category_section(
    category: str,
    items: list[dict],
    *,
    summary_cache_dir: str,
    model: str,
    variant: str | None,
    prompt_version: int,
    refresh: bool,
) -> dict:
    cache_path = category_summary_cache_path(
        summary_cache_dir,
        category,
        model=model,
        variant=variant,
        prompt_version=prompt_version,
    )
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    prompt = category_report_prompt(category, items)
    summary = await run_opencode_json(prompt, model=model, variant=variant)
    summary["model"] = model
    summary["variant"] = variant
    summary["prompt_version"] = prompt_version
    summary["entry_id"] = f"category::{category}"
    write_summary_cache_record(
        cache_path=cache_path,
        entry={
            "resolved_id": f"category::{category}",
            "title": f"Category Summary: {category}",
            "category": category,
        },
        prompt=prompt,
        files=[],
        summary=summary,
        model=model,
        variant=variant,
        prompt_version=prompt_version,
        summary_cache_dir=summary_cache_dir,
    )
    return summary


async def summarize_paper_entry(
    entry: dict,
    *,
    cache_dir: str,
    summary_cache_dir: str,
    markdown_cache_dir: str,
    model: str,
    variant: str | None,
    prompt_version: int,
    refresh: bool,
    db_path: str | None = None,
    prompt_kind: str = "paper",
) -> dict:
    citation_count = int(entry.get("citation_count") or 0)
    arxiv_id = str(entry.get("arxiv_id", ""))
    cache_path = summary_cache_path(
        summary_cache_dir,
        entry,
        model=model,
        variant=variant,
        prompt_version=prompt_version,
    )

    if db_path and arxiv_id and not refresh:
        from radar_db import get_db as _gdb, get_enrichment as _genr
        with _gdb(db_path) as _conn:
            existing = _genr(_conn, arxiv_id, model=model, prompt_version=prompt_version)
        if existing and existing["status"] == "completed":
            recorded_path = existing.get("summary_path", "")
            if recorded_path and Path(recorded_path).exists():
                return json.loads(Path(recorded_path).read_text(encoding="utf-8"))

    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    markdown_available = False
    try:
        markdown_path = await extract_markdown_from_pdf(
            entry,
            cache_dir=cache_dir,
            markdown_cache_dir=markdown_cache_dir,
            db_path=db_path,
        )
        files = [str(markdown_path)]
        markdown_available = True
    except Exception:
        pdf_path = entry.get("local_pdf_path") or str(cache_file_path(cache_dir, entry))
        if Path(pdf_path).exists():
            files = [pdf_path]
    if prompt_kind == "foundational":
        prompt = foundational_report_prompt(entry, citation_count)
    else:
        prompt = report_prompt(entry, citation_count)
    summary = await run_opencode_json(
        prompt,
        model=model,
        variant=variant,
        files=files,
    )
    summary["model"] = model
    summary["variant"] = variant
    summary["prompt_version"] = prompt_version
    summary["entry_id"] = entry.get("resolved_id") or entry.get("arxiv_id")
    summary.setdefault(
        "source_basis",
        "pdf_and_metadata" if markdown_available else "metadata_only",
    )
    summary.setdefault("confidence", "medium" if markdown_available else "low")
    summary.setdefault(
        "confidence_rationale",
        "Used extracted markdown from the paper PDF."
        if markdown_available
        else "Relied on metadata and abstract only.",
    )
    summary["_cache_path"] = str(cache_path)
    write_summary_cache_record(
        cache_path=cache_path,
        entry=entry,
        prompt=prompt,
        files=files,
        summary=summary,
        model=model,
        variant=variant,
        prompt_version=prompt_version,
        summary_cache_dir=summary_cache_dir,
    )
    return summary


def render_report_tex(
    title: str,
    manifest: dict,
    sections: list[dict],
    category_summaries: dict[str, dict],
) -> str:
    category_counts: dict[str, int] = {}
    for item in sections:
        category = str(item["entry"].get("category") or "Uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1

    top_cited = sorted(
        sections,
        key=lambda item: int(item["entry"].get("citation_count") or 0),
        reverse=True,
    )[:5]

    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{hyperref}",
        r"\begin{document}",
        rf"\title{{{latex_escape(title)}}}",
        rf"\date{{Generated {latex_escape(str(manifest.get('generated_at', '')))}}}",
        r"\maketitle",
        rf"\noindent Selected papers: {len(sections)}\\",
        rf"\noindent Storage root: \texttt{{{latex_escape(str(manifest.get('storage_root', '')))}}}",
        r"\tableofcontents",
        r"\bigskip",
    ]

    if not sections:
        lines.extend(
            [
                r"\section*{No Papers Selected}",
                r"The current manifest did not contain any selected papers.",
            ]
        )
    else:
        lines.extend(
            [
                r"\section{Executive Summary}",
                rf"This radar summary covers {len(sections)} selected papers across {len(category_counts)} categories.",
                r"\subsection*{Category Coverage}",
                r"\begin{itemize}",
            ]
        )
        for category, count in sorted(category_counts.items()):
            lines.append(rf"\item {latex_escape(category)}: {count} paper(s)")
        lines.extend(
            [
                r"\end{itemize}",
                r"\subsection*{Highest Citation Signal}",
                r"\begin{itemize}",
            ]
        )
        for item in top_cited:
            entry = item["entry"]
            lines.append(
                rf"\item {latex_escape(str(entry.get('title', 'Untitled')))} ({int(entry.get('citation_count') or 0)} citations)"
            )
        lines.extend(
            [
                r"\end{itemize}",
                r"\subsection*{What To Read First}",
                r"\begin{center}",
                r"\begin{tabular}{|r|p{7.5cm}|l|r|}",
                r"\hline",
                "Priority & Paper & Category & Citations " + r"\\",
                r"\hline",
            ]
        )
        for index, item in enumerate(top_cited, start=1):
            entry = item["entry"]
            lines.append(
                f"{index} & {latex_table_cell(str(entry.get('title', 'Untitled')))} & {latex_table_cell(str(entry.get('category', '')), 18)} & {int(entry.get('citation_count') or 0)} "
                + r"\\hline"
            )
        lines.extend([r"\end{tabular}", r"\end{center}", r"\bigskip"])

    grouped_sections: dict[str, list[dict]] = {}
    for item in sections:
        category = str(item["entry"].get("category") or "Uncategorized")
        grouped_sections.setdefault(category, []).append(item)

    for category, category_items in sorted(grouped_sections.items()):
        category_summary = category_summaries.get(category, {})
        lines.extend(
            [
                rf"\section{{{latex_escape(category)}}}",
                rf"This section contains {len(category_items)} paper(s) in {latex_escape(category)}.",
            ]
        )
        if category_summary:
            lines.extend(
                [
                    r"\subsection*{Category Executive Summary}",
                    latex_escape(str(category_summary.get("executive_summary", ""))),
                    r"\subsubsection*{Shared Themes}",
                    r"\begin{itemize}",
                ]
            )
            for item_text in category_summary.get("shared_themes", []):
                lines.append(rf"\item {latex_escape(str(item_text))}")
            lines.extend(
                [
                    r"\end{itemize}",
                    r"\subsubsection*{Intake Priorities}",
                    r"\begin{itemize}",
                ]
            )
            for item_text in category_summary.get("intake_priorities", []):
                lines.append(rf"\item {latex_escape(str(item_text))}")
            lines.extend([r"\end{itemize}"])
        for item in category_items:
            entry = item["entry"]
            summary = item["summary"]
            lines.extend(
                [
                    rf"\subsection{{{latex_escape(str(entry.get('title', 'Untitled')))}}}",
                    rf"\textbf{{arXiv:}} {latex_escape(str(entry.get('arxiv_id', '')))}\\",
                    rf"\textbf{{Incoming citations:}} {int(entry.get('citation_count') or 0)}\\",
                    rf"\textbf{{Published:}} {latex_escape(str(entry.get('published', ''))[:10])}\\",
                    rf"\textbf{{Area of progress:}} {latex_escape(str(summary.get('area_of_progress', '')))}",
                    rf"\textbf{{Evidence basis:}} {latex_escape(str(summary.get('source_basis', '')))}\\",
                    rf"\textbf{{Confidence:}} {confidence_badge(str(summary.get('confidence', 'unknown')))}\\",
                    latex_escape(str(summary.get("confidence_rationale", ""))),
                    r"\subsubsection*{Results Summary}",
                    latex_escape(str(summary.get("results_summary", ""))),
                    r"\subsubsection*{Intake Framing}",
                    latex_escape(str(summary.get("intake_framing", ""))),
                    r"\subsubsection*{Citation Signal}",
                    latex_escape(str(summary.get("citation_signal", ""))),
                    r"\subsubsection*{Primary Claims}",
                    r"\begin{itemize}",
                ]
            )
            for claim in summary.get("primary_claims", []):
                lines.append(rf"\item {latex_escape(str(claim))}")
            lines.extend(
                [
                    r"\end{itemize}",
                    r"\subsubsection*{Required Background}",
                    r"\begin{itemize}",
                ]
            )
            for item_text in summary.get("required_background", []):
                lines.append(rf"\item {latex_escape(str(item_text))}")
            lines.extend(
                [
                    r"\end{itemize}",
                    r"\subsubsection*{Background Research}",
                    r"\begin{itemize}",
                ]
            )
            for item_text in summary.get("background_research", []):
                lines.append(rf"\item {latex_escape(str(item_text))}")
            lines.extend(
                [
                    r"\end{itemize}",
                    r"\subsubsection*{Open Questions}",
                    r"\begin{itemize}",
                ]
            )
            for item_text in summary.get("open_questions", []):
                lines.append(rf"\item {latex_escape(str(item_text))}")
            lines.extend(
                [
                    r"\end{itemize}",
                    rf"\noindent\textbf{{Abstract URL:}} \url{{{str(entry.get('abs_url', ''))}}}\\",
                    rf"\noindent\textbf{{PDF URL:}} \url{{{str(entry.get('pdf_url', ''))}}}",
                    r"\medskip",
                ]
            )

    if sections:
        lines.extend(
            [r"\appendix", r"\section{Bibliography Appendix}", r"\begin{itemize}"]
        )
        for item in sections:
            entry = item["entry"]
            lines.append(
                rf"\item {latex_escape(str(entry.get('title', 'Untitled')))}. arXiv {latex_escape(str(entry.get('arxiv_id', '')))}. \url{{{str(entry.get('abs_url', ''))}}}"
            )
        lines.append(r"\end{itemize}")

    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


async def build_tex_pdf(tex_path: str, pdf_path: str, build_dir: str) -> None:
    tex_file = Path(tex_path)
    pdf_file = Path(pdf_path)
    build_path = Path(build_dir)
    build_path.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={build_path}",
            str(tex_file),
        ],
        cwd="/Users/jrepp/dev/boox-org",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    built_pdf = build_path / f"{tex_file.stem}.pdf"
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_pdf, pdf_file)


async def build_summary_report(
    manifest_path: str,
    *,
    cache_dir: str = DEFAULT_PDF_CACHE_DIR,
    model: str,
    variant: str | None,
    summary_cache_dir: str,
    markdown_cache_dir: str,
    prompt_version: int,
    title: str,
    max_papers: int | None,
    output_tex: str,
    output_pdf: str,
    build_dir: str,
    refresh_summaries: bool = False,
) -> dict:
    await prime_cache(manifest_path, cache_dir)
    manifest = load_manifest(manifest_path)
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    if max_papers is not None:
        entries = entries[:max_papers]

    sections = []
    for entry in entries:
        summary = await summarize_paper_entry(
            entry,
            cache_dir=cache_dir,
            summary_cache_dir=summary_cache_dir,
            markdown_cache_dir=markdown_cache_dir,
            model=model,
            variant=variant,
            prompt_version=prompt_version,
            refresh=refresh_summaries,
        )
        sections.append({"entry": entry, "summary": summary})

    grouped: dict[str, list[dict]] = {}
    for item in sections:
        grouped.setdefault(
            str(item["entry"].get("category") or "Uncategorized"), []
        ).append(item)
    category_summaries = {}
    for category, items in grouped.items():
        category_summaries[category] = await summarize_category_section(
            category,
            items,
            summary_cache_dir=summary_cache_dir,
            model=model,
            variant=variant,
            prompt_version=prompt_version,
            refresh=refresh_summaries,
        )

    tex = render_report_tex(title, manifest, sections, category_summaries)
    tex_path = Path(output_tex)
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(tex, encoding="utf-8")
    await build_tex_pdf(str(tex_path), output_pdf, build_dir)
    return {
        "manifest_path": manifest_path,
        "output_tex": output_tex,
        "output_pdf": output_pdf,
        "paper_count": len(sections),
        "model": model,
        "variant": variant,
        "summary_index": str(summary_index_path(summary_cache_dir)),
    }


def _paper_chapter_filename(entry: dict) -> str:
    arxiv_id = slugify(str(entry.get("resolved_id") or entry.get("arxiv_id", "unknown")))
    return f"{arxiv_id}.tex"


def _resolve_pdf_path(entry: dict, cache_dir: str) -> Path:
    return Path(cache_file_path(cache_dir, entry))


def _record_enrichment_to_db(
    db_path: str,
    entry: dict,
    summary: dict,
    model: str,
    variant: str | None,
    prompt_version: int,
    enrichment_type: str,
) -> None:
    from radar_db import get_db as _get_db, upsert_enrichment as _ue, EnrichmentRecord as _ER
    summary_path = summary.get("_cache_path", "")
    with _get_db(db_path) as _conn:
        _ue(_conn, _ER(
            arxiv_id=str(entry.get("arxiv_id", "")),
            enrichment_type=enrichment_type,
            model=model,
            variant=variant,
            prompt_version=prompt_version,
            status="completed",
            summary_path=summary_path,
            source_basis=str(summary.get("source_basis", "")),
            confidence=str(summary.get("confidence", "")),
            completed_at=datetime.now(UTC).isoformat(),
        ))


async def _ensure_pdf_cached(entry: dict, cache_dir: str) -> None:
    pdf_url = entry.get("pdf_url", "")
    if not pdf_url:
        return
    pdf_path = _resolve_pdf_path(entry, cache_dir)
    if pdf_path.exists():
        entry["local_pdf_path"] = str(pdf_path)
        return
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    def _download():
        import urllib.request as _ur
        req = _ur.Request(pdf_url, headers={"User-Agent": "arxiv-radar/0.1.0"})
        with _ur.urlopen(req, timeout=60) as resp:
            pdf_path.write_bytes(resp.read())

    await asyncio.to_thread(_download)
    entry["local_pdf_path"] = str(pdf_path)


def render_paper_chapter(entry: dict, summary: dict, foundational_refs: list[dict] | None = None) -> str:
    title = latex_escape(str(entry.get("title", "Untitled")))
    arxiv_id = str(entry.get("arxiv_id", ""))
    citations = int(entry.get("citation_count") or 0)
    published = str(entry.get("published", ""))[:10]
    authors = ", ".join((entry.get("authors") or [])[:3])
    if len(entry.get("authors") or []) > 3:
        authors += " et al."
    area = latex_escape(str(summary.get("area_of_progress", "")))
    confidence = str(summary.get("confidence", "unknown")).upper()
    results = latex_escape(str(summary.get("results_summary", "")))
    framing = latex_escape(str(summary.get("intake_framing", "")))
    citation_signal = latex_escape(str(summary.get("citation_signal", "")))
    abs_url = str(entry.get("abs_url", ""))
    pdf_url = str(entry.get("pdf_url", ""))

    lines = [
        r"\section*{" + f"{title}" + "}",
        r"\addcontentsline{toc}{section}{" + f"{title}" + "}",
        "",
        r"\papermeta{",
        rf"\textbf{{{authors}}} \quad | \quad {published} \quad | \quad {citations} citations \quad | \quad arXiv:{latex_escape(arxiv_id)}",
        r"}",
        "",
    ]
    if area:
        lines.append(rf"\areabox{{{area}}}\quad ")
    lines.append(rf"\confidencebadge{{{confidence}}}")
    lines.append("")

    lines.extend([
        r"\papercard{",
        rf"\insightbox{{{results}}}",
        r"\vspace{0.3em}",
        rf"\textbf{{Why it matters:}} {framing}",
        r"}",
        "",
    ])

    if citation_signal:
        lines.extend([
            r"\noindent\textbf{\color{mediumgray}Citation signal:} ",
            rf"{citation_signal}",
            "",
        ])

    claims = summary.get("primary_claims", [])
    if claims:
        lines.append(r"\noindent\textbf{\color{darkgray}Key Claims}")
        lines.append(r"\begin{itemize}")
        for claim in claims[:5]:
            lines.append(rf"\item {latex_escape(str(claim))}")
        lines.extend([r"\end{itemize}", ""])

    bg = summary.get("required_background", [])
    if bg:
        lines.append(r"\noindent\textbf{\color{mediumgray}Background Needed}")
        lines.append(r"\begin{itemize}")
        for item_text in bg[:4]:
            lines.append(rf"\item {latex_escape(str(item_text))}")
        lines.extend([r"\end{itemize}", ""])

    oq = summary.get("open_questions", [])
    if oq:
        lines.append(r"\noindent\textbf{\color{mediumgray}Open Questions}")
        lines.append(r"\begin{itemize}")
        for item_text in oq[:3]:
            lines.append(rf"\item {latex_escape(str(item_text))}")
        lines.extend([r"\end{itemize}", ""])

    if foundational_refs:
        lines.extend([
            r"\noindent\textbf{\color{mediumgray}Foundational References}",
            r"\begin{itemize}",
        ])
        for ref in foundational_refs[:6]:
            ref_title = latex_escape(str(ref.get("title", "Unknown")))
            ref_year = ref.get("publication_year", "")
            ref_cites = int(ref.get("citation_count") or 0)
            lines.append(rf"\item {ref_title} ({ref_year}, {ref_cites} citations)")
        lines.extend([r"\end{itemize}", ""])

    lines.extend([
        r"\noindent{\footnotesize",
        rf"\textcolor{{accentblue}}{{\url{{{abs_url}}}}}",
        r"}",
        r"\medskip",
        "",
    ])
    return "\n".join(lines) + "\n"


def render_executive_summary_chapter(
    sections: list[dict],
    category_summaries: dict[str, dict],
    manifest: dict,
) -> str:
    category_counts: dict[str, int] = {}
    for item in sections:
        cat = str(item["entry"].get("category") or "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    top_cited = sorted(
        sections,
        key=lambda item: int(item["entry"].get("citation_count") or 0),
        reverse=True,
    )[:5]

    lines = [
        r"\section*{In This Issue}",
        r"\addcontentsline{toc}{section}{In This Issue}",
        "",
        rf"{len(sections)} selected papers across {len(category_counts)} categories.",
        "",
    ]

    if top_cited:
        lines.extend([
            r"\noindent\textbf{\color{darkgray}Highest Citation Signal}",
            r"\begin{itemize}",
        ])
        for item in top_cited:
            entry = item["entry"]
            title = latex_escape(str(entry.get("title", "Untitled")))
            cites = int(entry.get("citation_count") or 0)
            lines.append(rf"\item {title} ({cites} citations)")
        lines.extend([r"\end{itemize}", ""])

    for category, cat_summary in sorted(category_summaries.items()):
        lines.extend([
            rf"\section*{{{latex_escape(category)} Overview}}",
            rf"\addcontentsline{{toc}}{{section}}{{{latex_escape(category)} Overview}}",
            "",
            latex_escape(str(cat_summary.get("executive_summary", ""))),
            "",
        ])
        themes = cat_summary.get("shared_themes", [])
        if themes:
            lines.append(r"\noindent\textbf{\color{mediumgray}Themes}")
            lines.append(r"\begin{itemize}")
            for theme in themes[:4]:
                lines.append(rf"\item {latex_escape(str(theme))}")
            lines.extend([r"\end{itemize}", ""])
        priorities = cat_summary.get("intake_priorities", [])
        if priorities:
            lines.append(r"\noindent\textbf{\color{mediumgray}Reading Order}")
            lines.append(r"\begin{itemize}")
            for priority in priorities[:4]:
                lines.append(rf"\item {latex_escape(str(priority))}")
            lines.extend([r"\end{itemize}", ""])

    return "\n".join(lines) + "\n"


def render_bibliography_chapter(
    sections: list[dict],
    foundational_sections: list[dict] | None = None,
) -> str:
    lines = [r"\section*{Bibliography}", r"\addcontentsline{toc}{section}{Bibliography}", ""]
    grouped: dict[str, list[dict]] = {}
    for item in sections:
        cat = str(item["entry"].get("category") or "Uncategorized")
        grouped.setdefault(cat, []).append(item)
    for category, items in sorted(grouped.items()):
        lines.extend([rf"\section{{{latex_escape(category)}}}", r"\begin{itemize}"])
        for item in items:
            entry = item["entry"]
            title = latex_escape(str(entry.get("title", "Untitled")))
            arxiv_id = latex_escape(str(entry.get("arxiv_id", "")))
            url = str(entry.get("abs_url", ""))
            lines.append(rf"\item {title}. arXiv {arxiv_id}. \url{{{url}}}")
        lines.extend([r"\end{itemize}", ""])
    if foundational_sections:
        lines.extend([r"\section{Foundational References}", r"\begin{itemize}"])
        for item in foundational_sections:
            entry = item["entry"]
            title = latex_escape(str(entry.get("title", "Untitled")))
            cites = int(entry.get("citation_count") or 0)
            year = entry.get("publication_year", "")
            url = str(entry.get("abs_url", ""))
            lines.append(rf"\item {title} ({year}, {cites} citations). \url{{{url}}}")
        lines.extend([r"\end{itemize}", ""])
    return "\n".join(lines) + "\n"


def render_foundational_chapter(foundational_sections: list[dict]) -> str:
    lines = [
        r"\section*{Foundational References}",
        "",
        r"These foundational papers were discovered through 2-hop citation traversal of the primary radar selections. "
        r"Each paper received a full intake summary to help build foundational knowledge.",
        "",
    ]
    for item in foundational_sections:
        entry = item["entry"]
        summary = item["summary"]
        title = latex_escape(str(entry.get("title", "Untitled")))
        citations = int(entry.get("citation_count") or 0)
        year = entry.get("publication_year", "")
        abs_url = str(entry.get("abs_url", ""))
        results = latex_escape(str(summary.get("results_summary", "")))
        area = latex_escape(str(summary.get("area_of_progress", "")))
        confidence = str(summary.get("confidence", "unknown")).upper()
        lines.extend(
            [
                r"\section*{" + f"{title}" + "}",
                rf"\noindent\textbf{{Year:}} {year}\quad \textbf{{Citations:}} {citations}\quad \textbf{{Confidence:}} [{confidence}]",
                "",
                rf"\textit{{{area}}}" if area else "",
                results,
                "",
            ]
        )
        if summary.get("primary_claims"):
            lines.extend([r"\begin{itemize}"])
            for claim in summary.get("primary_claims", []):
                lines.append(rf"\item {latex_escape(str(claim))}")
            lines.extend([r"\end{itemize}", ""])
        if summary.get("required_background"):
            lines.extend([r"\noindent\textbf{Required Background:}", r"\begin{itemize}"])
            for bg in summary.get("required_background", []):
                lines.append(rf"\item {latex_escape(str(bg))}")
            lines.extend([r"\end{itemize}", ""])
        if summary.get("long_range_insights"):
            lines.extend([r"\noindent\textbf{Long-Range Insights:}", r"\begin{itemize}"])
            for insight in summary.get("long_range_insights", []):
                lines.append(rf"\item {latex_escape(str(insight))}")
            lines.extend([r"\end{itemize}", ""])
        if summary.get("modern_connections"):
            lines.extend([r"\noindent\textbf{Modern Connections:}", r"\begin{itemize}"])
            for connection in summary.get("modern_connections", []):
                lines.append(rf"\item {latex_escape(str(connection))}")
            lines.extend([r"\end{itemize}", ""])
        if abs_url:
            lines.extend([rf"\noindent\url{{{abs_url}}}", ""])
        lines.append(r"\medskip")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_periodical_main_tex(
    *,
    title: str,
    chapter_includes: list[str],
) -> str:
    includes = "\n".join(rf"\include{{chapters/{name}}}" for name in chapter_includes)
    return (
        r"%% Research Radar Periodical"
        "\n"
        r"%% Auto-generated -- do not edit directly"
        "\n"
        r"\documentclass{research-radar}"
        "\n\n"
        rf"\reporttitle{{{latex_escape(title)}}}"
        "\n"
        r"\reportstatus{Draft}"
        "\n"
        rf"\issueinfo{{\today}}"
        "\n\n"
        r"\begin{document}"
        "\n\n"
        r"\thispagestyle{cover}"
        "\n\n"
        r"\begin{tikzpicture}[remember picture,overlay]"
        "\n"
        r"  \fill[coverbg] (current page.north west) rectangle (current page.south east);"
        "\n"
        r"  \node[anchor=north west, text width=0.85\paperwidth, inner sep=0pt]"
        "\n"
        r"    at ([shift={(0.6in,-0.8in)}]current page.north west) {"
        "\n"
        r"      {\color{coveraccent}\Large\bfseries\MakeUppercase{Research Radar}}"
        "\n"
        r"      \\[0.6em]"
        r"      {\color{white}\rule{4cm}{0.6pt}}"
        "\n"
        r"      \\[0.8em]"
        rf"      {{\color{{white}}\huge\bfseries {latex_escape(title)}}}"
        "\n"
        r"      \\[1.2em]"
        r"      {\color{white!70}\large AI and Data Research Periodical}"
        "\n"
        r"      \\[0.3em]"
        r"      {\color{white!50}\normalsize \today}"
        "\n"
        r"    };"
        "\n"
        r"\end{tikzpicture}"
        "\n\n"
        r"\vspace{6in}"
        "\n"
        r"\newpage"
        "\n\n"
        r"\thispagestyle{plain}"
        "\n"
        r"\tableofcontents"
        "\n"
        r"\newpage"
        "\n\n"
        f"{includes}\n\n"
        r"\appendix"
        "\n"
        r"\include{chapters/bibliography}"
        "\n\n"
        r"\end{document}"
        "\n"
    )


async def build_periodical(
    manifest_path: str,
    *,
    cache_dir: str = DEFAULT_PDF_CACHE_DIR,
    model: str,
    variant: str | None,
    summary_cache_dir: str,
    markdown_cache_dir: str,
    prompt_version: int,
    title: str,
    max_papers: int | None,
    periodical_dir: str = "tex/research-radar",
    refresh_summaries: bool = False,
    reference_depth: int = 2,
    max_references_per_paper: int = 10,
    min_reference_citations: int = 50,
    db_path: str | None = None,
) -> dict:
    build_id = None
    if db_path:
        from radar_db import init_db as _init, get_db as _get_db
        _init(db_path)

    await prime_cache(manifest_path, cache_dir)
    manifest = load_manifest(manifest_path)
    entries = [
        entry for entry in manifest.get("entries", []) if isinstance(entry, dict)
    ]
    if max_papers is not None:
        entries = entries[:max_papers]

    manifest_sha256 = hashlib.sha256(
        Path(manifest_path).read_bytes()
    ).hexdigest()

    if db_path:
        from radar_db import start_periodical_build as _sp, upsert_paper as _up, entry_to_paper as _e2p, record_periodical_paper as _rpp
        with _get_db(db_path) as _conn:
            build_id = _sp(_conn, paper_count=len(entries), reference_depth=reference_depth, model=model, manifest_sha256=manifest_sha256)
            for entry in entries:
                try:
                    _up(_conn, _e2p(entry, discovered_via="periodical"))
                except Exception:
                    pass
                _rpp(_conn, build_id, str(entry.get("arxiv_id", "")), "primary")

    sections = []
    for entry in entries:
        summary = await summarize_paper_entry(
            entry,
            cache_dir=cache_dir,
            summary_cache_dir=summary_cache_dir,
            markdown_cache_dir=markdown_cache_dir,
            model=model,
            variant=variant,
            prompt_version=prompt_version,
            refresh=refresh_summaries,
            db_path=db_path,
        )
        sections.append({"entry": entry, "summary": summary})
        if db_path:
            _record_enrichment_to_db(db_path, entry, summary, model, variant, prompt_version, "llm_summary")

    grouped: dict[str, list[dict]] = {}
    for item in sections:
        grouped.setdefault(
            str(item["entry"].get("category") or "Uncategorized"), []
        ).append(item)
    category_summaries = {}
    for category, items in grouped.items():
        category_summaries[category] = await summarize_category_section(
            category,
            items,
            summary_cache_dir=summary_cache_dir,
            model=model,
            variant=variant,
            prompt_version=prompt_version,
            refresh=refresh_summaries,
        )

    ref_map: dict[str, list[dict]] = {}
    foundational_sections: list[dict] = []
    if reference_depth > 0:
        typer.echo(f"Traversing references (depth={reference_depth}, max_refs={max_references_per_paper}, min_cites={min_reference_citations})...")
        ref_map = await asyncio.to_thread(
            traverse_references,
            entries,
            max_depth=reference_depth,
            max_refs_per_paper=max_references_per_paper,
            min_citations=min_reference_citations,
        )
        unique_foundational: dict[str, dict] = {}
        for _src, refs in ref_map.items():
            for ref in refs:
                key = ref.get("openalex_id") or ref.get("arxiv_id")
                if key and key not in unique_foundational:
                    unique_foundational[key] = ref
        typer.echo(f"Discovered {len(unique_foundational)} unique foundational papers")
        if db_path:
            from radar_db import get_db as _get_db2, upsert_paper as _up2, entry_to_paper as _e2p2, record_reference_edges as _rre
            with _get_db2(db_path) as _conn:
                for fref in unique_foundational.values():
                    if fref.get("arxiv_id") and fref["arxiv_id"].startswith(("1", "2")):
                        try:
                            _up2(_conn, _e2p2(fref, discovered_via="reference_traversal"))
                        except Exception:
                            pass
                for src, refs in ref_map.items():
                    try:
                        _rre(_conn, normalize_arxiv_id(src), refs)
                    except Exception:
                        pass
        for fentry in sorted(
            unique_foundational.values(),
            key=lambda e: int(e.get("citation_count") or 0),
            reverse=True,
        ):
            if fentry.get("has_pdf") and fentry.get("arxiv_id"):
                try:
                    await _ensure_pdf_cached(fentry, cache_dir)
                    await extract_markdown_from_pdf(
                        fentry,
                        cache_dir=cache_dir,
                        markdown_cache_dir=markdown_cache_dir,
                        db_path=db_path,
                    )
                    fentry["local_pdf_path"] = str(_resolve_pdf_path(fentry, cache_dir))
                except Exception:
                    pass
            try:
                fsummary = await summarize_paper_entry(
                    fentry,
                    cache_dir=cache_dir,
                    summary_cache_dir=summary_cache_dir,
                    markdown_cache_dir=markdown_cache_dir,
                    model=model,
                    variant=variant,
                    prompt_version=prompt_version,
                    refresh=refresh_summaries,
                    db_path=db_path,
                    prompt_kind="foundational",
                )
                foundational_sections.append({"entry": fentry, "summary": fsummary})
                if db_path:
                    _record_enrichment_to_db(db_path, fentry, fsummary, model, variant, prompt_version, "foundational_summary")
            except Exception as exc:
                typer.echo(f"  Skipped {fentry.get('title', '?')[:50]}: {exc}", err=True)
                foundational_sections.append({
                    "entry": fentry,
                    "summary": {
                        "results_summary": "Summary unavailable.",
                        "area_of_progress": "",
                        "confidence": "low",
                        "confidence_rationale": "LLM summarization failed.",
                        "primary_claims": [],
                        "required_background": [],
                        "background_research": [],
                        "open_questions": [],
                    },
                })

    periodical_path = Path(periodical_dir)
    chapters_dir = periodical_path / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    exec_chapter = render_executive_summary_chapter(
        sections, category_summaries, manifest
    )
    (chapters_dir / "executive-summary.tex").write_text(exec_chapter, encoding="utf-8")

    bib_chapter = render_bibliography_chapter(sections, foundational_sections)
    (chapters_dir / "bibliography.tex").write_text(bib_chapter, encoding="utf-8")

    chapter_includes = ["executive-summary"]

    grouped_sections: dict[str, list[dict]] = {}
    for item in sections:
        cat = str(item["entry"].get("category") or "Uncategorized")
        grouped_sections.setdefault(cat, []).append(item)

    for category, items in sorted(grouped_sections.items()):
        chapter_includes.append(slugify(category))
        cat_lines = [
            rf"\section{{{latex_escape(category)}}}",
            "",
        ]
        for item in items:
            entry = item["entry"]
            filename = _paper_chapter_filename(entry)
            arxiv_id = str(entry.get("resolved_id") or entry.get("arxiv_id", ""))
            paper_refs = ref_map.get(arxiv_id, [])
            paper_tex = render_paper_chapter(entry, item["summary"], paper_refs)
            (chapters_dir / filename).write_text(paper_tex, encoding="utf-8")
            cat_lines.append(rf"\input{{chapters/{filename.replace('.tex', '')}}}")
        (chapters_dir / f"{slugify(category)}.tex").write_text(
            "\n".join(cat_lines) + "\n", encoding="utf-8"
        )

    if foundational_sections:
        chapter_includes.append("foundational-references")
        found_tex = render_foundational_chapter(foundational_sections)
        (chapters_dir / "foundational-references.tex").write_text(found_tex, encoding="utf-8")

    main_tex = render_periodical_main_tex(
        title=title,
        chapter_includes=chapter_includes,
    )
    main_tex_path = periodical_path / "research-radar.tex"
    main_tex_path.write_text(main_tex, encoding="utf-8")

    dist_dir = periodical_path / ".." / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    build_result = await asyncio.to_thread(
        subprocess.run,
        [
            "just",
            "build",
        ],
        cwd=str(periodical_path),
        capture_output=True,
        text=True,
    )
    if build_result.returncode != 0:
        raise RuntimeError(build_result.stderr or build_result.stdout)

    output_pdf = str(dist_dir.resolve() / "research-radar.pdf")
    if db_path and build_id:
        from radar_db import get_db as _get_db3, finish_periodical_build as _fpb, record_periodical_paper as _rpp2, upsert_paper as _up3, entry_to_paper as _e2p3
        with _get_db3(db_path) as _conn:
            for fs in foundational_sections:
                fa_id = str(fs["entry"].get("arxiv_id", ""))
                if fa_id:
                    try:
                        _up3(_conn, _e2p3(fs["entry"], discovered_via="reference_traversal"))
                    except Exception:
                        pass
                    _rpp2(_conn, build_id, fa_id, "foundational")
            _fpb(_conn, build_id, paper_count=len(sections), foundational_count=len(foundational_sections), output_pdf=output_pdf)
    return {
        "manifest_path": manifest_path,
        "periodical_dir": str(periodical_path),
        "output_pdf": output_pdf,
        "paper_count": len(sections),
        "foundational_count": len(foundational_sections),
        "model": model,
        "variant": variant,
        "chapter_count": len(chapter_includes),
        "summary_index": str(summary_index_path(summary_cache_dir)),
    }


class RadarEntryListItem(ListItem):
    def __init__(self, item_data: dict) -> None:
        self.item_data = item_data
        self.label_widget = Static()
        super().__init__(self.label_widget)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        entry = self.item_data["entry"]
        prefix = "[x]" if selected else "[ ]"
        section = "new" if self.item_data["section"] == "recent" else "cited"
        citations = entry.get("citation_count")
        citation_text = f" / cites={citations}" if citations is not None else ""
        self.label_widget.update(
            f"{prefix} {self.item_data['category']} / {section} / {entry['arxiv_id']} / {entry['title']}{citation_text}"
        )


class RadarCuratorApp(TextualApp[None]):
    CSS = """
    #entries {
      width: 1fr;
      height: 1fr;
      border: round $accent;
    }
    #details {
      width: 1fr;
      height: 1fr;
      border: round $primary;
      padding: 1 2;
      overflow-y: auto;
    }
    """

    BINDINGS = [
        ("space", "toggle", "Toggle"),
        ("c", "sort_citations", "Sort Citations"),
        ("e", "export", "Export"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, report: dict, report_path: Path, output_path: Path) -> None:
        super().__init__()
        self.report = report
        self.report_path = report_path
        self.output_path = output_path
        self.items = flatten_radar_report(report)
        self.selected_keys: set[str] = set()
        self.exported = False
        self.sort_mode = "default"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ListView(
                *(RadarEntryListItem(item) for item in self.items), id="entries"
            )
            yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Research Radar Curator"
        self._update_subtitle()
        self._refresh_details()

    def _update_subtitle(self) -> None:
        self.sub_title = f"selected={len(self.selected_keys)} sort={self.sort_mode} export={self.output_path}"

    def _sorted_items(self) -> list[dict]:
        if self.sort_mode == "citations":
            return sorted(
                self.items,
                key=lambda item: (
                    int(item["entry"].get("citation_count") or 0),
                    item["entry"].get("published", ""),
                    item["entry"].get("title", ""),
                ),
                reverse=True,
            )
        return list(self.items)

    async def _render_list(self, preserve_key: str | None = None) -> None:
        list_view = self.query_one("#entries", ListView)
        rendered_items = self._sorted_items()
        await list_view.clear()
        await list_view.extend(RadarEntryListItem(item) for item in rendered_items)
        for child in list_view.children:
            if isinstance(child, RadarEntryListItem):
                child.set_selected(child.item_data["key"] in self.selected_keys)

        target_index = 0
        if preserve_key is not None:
            for index, item in enumerate(rendered_items):
                if item["key"] == preserve_key:
                    target_index = index
                    break
        list_view.index = target_index if rendered_items else None

    def _current_item(self) -> RadarEntryListItem | None:
        list_view = self.query_one("#entries", ListView)
        index = list_view.index
        if index is None or index < 0 or index >= len(list_view.children):
            return None
        item = list_view.children[index]
        return item if isinstance(item, RadarEntryListItem) else None

    def _refresh_details(self) -> None:
        details = self.query_one("#details", Static)
        item = self._current_item()
        if item is None:
            details.update("No radar entries available.")
            return
        entry = item.item_data["entry"]
        lines = [
            f"Category: {item.item_data['category']}",
            f"Section: {item.item_data['section']}",
            f"arXiv: {entry['arxiv_id']}",
            f"Published: {entry['published'][:10]}",
            f"Title: {entry['title']}",
            f"Authors: {', '.join(entry.get('authors', []))}",
            f"Target: {entry['target_path']}",
        ]
        if "citation_count" in entry:
            lines.append(f"Citations: {entry['citation_count']}")
        if entry.get("citation_source_url"):
            lines.append(f"Citation source: {entry['citation_source_url']}")
        lines.extend(["", entry.get("summary", "")])
        details.update("\n".join(lines))

    def on_list_view_highlighted(self, _: ListView.Highlighted) -> None:
        self._refresh_details()

    def action_toggle(self) -> None:
        item = self._current_item()
        if item is None:
            return
        key = item.item_data["key"]
        if key in self.selected_keys:
            self.selected_keys.remove(key)
            item.set_selected(False)
        else:
            self.selected_keys.add(key)
            item.set_selected(True)
        self._update_subtitle()
        self._refresh_details()

    async def action_sort_citations(self) -> None:
        current = self._current_item()
        preserve_key = current.item_data["key"] if current is not None else None
        self.sort_mode = "citations" if self.sort_mode == "default" else "default"
        await self._render_list(preserve_key)
        self._update_subtitle()
        self._refresh_details()

    def action_export(self) -> None:
        selected_items = [
            item for item in self.items if item["key"] in self.selected_keys
        ]
        if not selected_items:
            self.bell()
            return
        manifest = curated_manifest(self.report, self.report_path, selected_items)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.exported = True
        self.exit()


def extract_arxiv_id(raw_value: str) -> str | None:
    value = raw_value.strip().rstrip(",")
    if not value:
        return None
    if value.endswith(".pdf"):
        value = value[:-4]
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        if "/abs/" in path:
            value = path.split("/abs/", 1)[1]
        elif "/pdf/" in path:
            value = path.split("/pdf/", 1)[1]
        else:
            value = path.rsplit("/", 1)[-1]
    match = ARXIV_ID_PATTERN.search(value.strip())
    return match.group("id") if match else None


def normalize_arxiv_id(arxiv_id: str) -> str:
    return radar_core.normalize_arxiv_id(arxiv_id)


def load_arxiv_ids(input_path: str | None, ids: list[str]) -> list[str]:
    tokens: list[str] = []
    if input_path:
        for raw_line in Path(input_path).read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            for token in re.split(r"[\s,]+", line):
                if token:
                    tokens.append(token)
    tokens.extend(ids)

    resolved: list[str] = []
    invalid: list[str] = []
    for token in tokens:
        arxiv_id = extract_arxiv_id(token)
        if arxiv_id:
            resolved.append(arxiv_id)
        else:
            invalid.append(token)
    if invalid:
        joined = ", ".join(invalid)
        raise ValueError(f"could not parse arXiv id(s): {joined}")
    if not resolved:
        raise ValueError("no arXiv ids provided")
    return resolved


def fetch_arxiv_entries(arxiv_ids: list[str]) -> list[ArxivEntry]:
    query = urllib.parse.urlencode({"id_list": ",".join(arxiv_ids)})
    request = urllib.request.Request(
        f"{ARXIV_API_URL}?{query}",
        headers={"Accept": "application/atom+xml"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())

    entries_by_id: dict[str, ET.Element] = {}
    entries_by_base: dict[str, ET.Element] = {}
    for entry in root.findall("atom:entry", ARXIV_XML_NS):
        abs_url = entry.findtext("atom:id", default="", namespaces=ARXIV_XML_NS).strip()
        resolved_id = abs_url.rsplit("/", 1)[-1]
        if resolved_id:
            entries_by_id[resolved_id] = entry
            entries_by_base[normalize_arxiv_id(resolved_id)] = entry

    resolved_entries: list[ArxivEntry] = []
    missing: list[str] = []
    for requested_id in arxiv_ids:
        entry = entries_by_id.get(requested_id)
        if entry is None:
            entry = entries_by_base.get(normalize_arxiv_id(requested_id))
        if entry is None:
            missing.append(requested_id)
            continue

        abs_url = entry.findtext("atom:id", default="", namespaces=ARXIV_XML_NS).strip()
        resolved_id = abs_url.rsplit("/", 1)[-1]
        title = " ".join(
            entry.findtext("atom:title", default="", namespaces=ARXIV_XML_NS).split()
        )
        summary = " ".join(
            entry.findtext("atom:summary", default="", namespaces=ARXIV_XML_NS).split()
        )
        pdf_url = ""
        for link in entry.findall("atom:link", ARXIV_XML_NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "").strip()
                break
        if pdf_url and not pdf_url.endswith(".pdf"):
            pdf_url = f"{pdf_url}.pdf"
        suggested_filename = f"{sanitize_filename(title)}.pdf"
        resolved_entries.append(
            ArxivEntry(
                requested_id=requested_id,
                resolved_id=resolved_id,
                title=title,
                pdf_url=pdf_url,
                abs_url=abs_url,
                summary=summary,
                suggested_filename=suggested_filename,
                target_path="",
            )
        )

    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"arXiv metadata not found for: {joined}")
    return resolved_entries


def build_arxiv_manifest(
    arxiv_ids: list[str], category: str, storage_root: str
) -> dict:
    entries = fetch_arxiv_entries(arxiv_ids)
    category_root = str(PurePosixPath(storage_root) / category)
    manifest_entries = []
    physical_targets = []
    for entry in entries:
        target_path = str(PurePosixPath(category_root) / entry.suggested_filename)
        physical_targets.append(target_path)
        manifest_entries.append(
            {
                "requested_id": entry.requested_id,
                "resolved_id": entry.resolved_id,
                "title": entry.title,
                "pdf_url": entry.pdf_url,
                "abs_url": entry.abs_url,
                "summary": entry.summary,
                "suggested_filename": entry.suggested_filename,
                "target_path": target_path,
            }
        )

    return {
        "source": "arxiv",
        "generated_at": datetime.now(UTC).isoformat(),
        "storage_root": storage_root,
        "category": category,
        "entries": manifest_entries,
        "sync_contract": {
            "kind": "library_sync_contract",
            "version": 1,
            "storage_root": storage_root,
            "categories": {
                category: {
                    "physical_targets": physical_targets,
                    "shelf_targets": physical_targets,
                }
            },
        },
    }


def write_json_output(payload: dict, output_path: str | None) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if output_path:
        Path(output_path).write_text(rendered, encoding="utf-8")
        print(f"wrote {output_path}")
    else:
        sys.stdout.write(rendered)


@dataclass
class SessionPlan:
    missing_storage_folders: list[str] = field(default_factory=list)
    storage_moves: dict[str, list[str]] = field(default_factory=dict)
    missing_shelves: list[str] = field(default_factory=list)
    shelf_moves: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def has_changes(self) -> bool:
        return any(
            [
                self.missing_storage_folders,
                self.storage_moves,
                self.missing_shelves,
                self.shelf_moves,
            ]
        )


class BooxClient:
    def __init__(
        self, host: str, token: str | None = None, password: str | None = None
    ) -> None:
        self.host = host.rstrip("/")
        self.ws_host = self.host.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1
        )
        if password and not token:
            token = base64.b64encode(f":{password}".encode("utf-8")).decode("ascii")
        self.headers = {}
        if token:
            self.headers["Authorization"] = f"Basic {token}"
        self.device_id: str | None = None

    async def init(self) -> None:
        info = await self.device_info()
        self.device_id = info["id"]

    async def _http_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict[str, str] | None = None,
    ) -> dict:
        def _request() -> dict:
            url = f"{self.host}{path}"
            if query:
                url = f"{url}?{urllib.parse.urlencode(query)}"
            data = None
            headers = {"Accept": "application/json", **self.headers}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                url, data=data, method=method, headers=headers
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                detail = body or exc.reason
                raise RuntimeError(
                    f"{method} {path} failed with {exc.code}: {detail}"
                ) from exc

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await asyncio.to_thread(_request)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(2)
        assert last_error is not None
        raise last_error

    async def _http_bytes(
        self,
        method: str,
        path: str,
        data: bytes,
        headers: dict[str, str],
    ) -> bytes:
        def _request() -> bytes:
            url = f"{self.host}{path}"
            request = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={**self.headers, **headers},
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                detail = body or exc.reason
                raise RuntimeError(
                    f"{method} {path} failed with {exc.code}: {detail}"
                ) from exc

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await asyncio.to_thread(_request)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(2)
        assert last_error is not None
        raise last_error

    async def _ws_api(
        self, api_path: str, method: str = "GET", params: dict | None = None
    ) -> dict:
        if not self.device_id:
            await self.init()
        message = {
            "to": self.device_id,
            "from": "boox-sync",
            "type": "request",
            "action": "api",
            "data": {"path": api_path, "method": method, "params": params or {}},
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with websockets.connect(self.ws_host) as websocket:
                    await websocket.send(json.dumps(message))
                    while True:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=20)
                        outer = json.loads(raw)
                        if outer.get("action") != "api":
                            continue
                        inner = json.loads(outer["data"])
                        if inner.get("path") != api_path:
                            continue
                        payload = inner.get("data")
                        return (
                            json.loads(payload) if isinstance(payload, str) else payload
                        )
            except (TimeoutError, OSError, websockets.WebSocketException) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(2)
        assert last_error is not None
        raise last_error

    async def device_info(self) -> dict:
        return await self._http_json("GET", "/api/device")

    async def storage_list(self, dir_path: str) -> list[dict]:
        payload = await self._ws_api(
            "api/storage",
            "GET",
            {
                "args": {
                    "limit": 200,
                    "offset": 0,
                    "dir": dir_path,
                    "refresh": False,
                    "sortBy": "CreationTime",
                    "sortOrder": "Desc",
                }
            },
        )
        return payload["data"]["list"]

    async def library_list(self, library_unique_id: str | None = None) -> dict:
        payload = await self._ws_api(
            "api/library",
            "GET",
            {
                "args": {
                    "limit": 200,
                    "offset": 0,
                    "libraryUniqueId": library_unique_id,
                    "sortBy": "CreationTime",
                    "order": "Desc",
                }
            },
        )
        return payload

    async def library_tree(self) -> dict:
        return await self._http_json("GET", "/api/library/tree")

    async def create_storage_folder(self, parent: str, name: str) -> dict:
        return await self._http_json(
            "POST", "/api/storage/directory", {"parent": parent, "name": name}
        )

    async def upload_storage_file(
        self,
        dir_path: str,
        file_name: str,
        content: bytes,
        sender: str = "boox-sync",
    ) -> dict:
        boundary = f"----booxsync{uuid.uuid4().hex}"
        parts = [
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="dir"\r\n\r\n'
                f"{dir_path}\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="sender"\r\n\r\n'
                f"{sender}\r\n"
            ).encode("utf-8"),
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                "Content-Type: application/pdf\r\n\r\n"
            ).encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        response = await self._http_bytes(
            "POST",
            "/api/storage/upload",
            b"".join(parts),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if not response:
            return {}
        text = response.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def move_storage_files(
        self, parent: str, selected: list[str], force: bool = False
    ) -> dict:
        payload = {
            "force": force,
            "parent": parent,
            "map": {
                "selectedMap": {
                    "null": {
                        "count": 0,
                        "selectedAllMode": False,
                        "selectedList": selected,
                    }
                }
            },
        }
        return await self._http_json("POST", "/api/storage/file/move", payload)

    async def create_shelf(self, name: str, parent: str | None = None) -> dict:
        return await self._http_json(
            "POST", "/api/library", {"parent": parent, "name": name}
        )

    async def move_library_files(
        self,
        shelf_name: str,
        shelf_id: str,
        selected: list[str],
        parent_id: str | None = None,
        force: bool = False,
    ) -> dict:
        payload = {
            "library": {"name": shelf_name, "idString": shelf_id},
            "force": force,
            "map": {
                "selectedMap": {
                    "null" if parent_id is None else parent_id: {
                        "count": 0,
                        "selectedAllMode": False,
                        "selectedList": selected,
                    }
                }
            },
        }
        return await self._http_json("POST", "/api/library/move", payload)


async def gather_state(client: BooxClient, spec: PlanSpec) -> dict:
    books_root = await client.storage_list(spec.storage_root)

    topic_dirs = {
        basename(item["path"]): item["path"]
        for item in books_root
        if item.get("dir") and basename(item["path"]) in spec.category_names
    }

    listings: dict[str, list[dict]] = {spec.storage_root: books_root}
    discovered_files: dict[str, str] = {}

    for dir_path in spec.scan_dirs:
        if dir_path not in listings:
            listings[dir_path] = await client.storage_list(dir_path)
        for item in listings[dir_path]:
            if not item.get("dir"):
                discovered_files[basename(item["path"])] = item["path"]

    for dir_path in topic_dirs.values():
        if dir_path not in listings:
            listings[dir_path] = await client.storage_list(dir_path)
        for item in listings[dir_path]:
            if not item.get("dir"):
                discovered_files[basename(item["path"])] = item["path"]

    tree = await client.library_tree()
    shelves = {
        child["library"]["name"]: child["library"]["idString"]
        for child in tree.get("children", [])
    }

    current_shelf_for_id: dict[str, str | None] = {}
    root_library = await client.library_list(None)
    for book in root_library.get("visibleBookList", []):
        metadata = book["metadata"]
        current_shelf_for_id[metadata["idString"]] = None
        discovered_files.setdefault(
            basename(metadata["location"]), metadata["location"]
        )

    for shelf_name, shelf_id in shelves.items():
        shelf_payload = await client.library_list(shelf_id)
        for book in shelf_payload.get("visibleBookList", []):
            metadata = book["metadata"]
            current_shelf_for_id[metadata["idString"]] = shelf_name
            discovered_files.setdefault(
                basename(metadata["location"]), metadata["location"]
            )

    download_root = listings.get(spec.scan_dirs[1]) if len(spec.scan_dirs) > 1 else []
    return {
        "books_root": books_root,
        "download_root": download_root,
        "topic_dirs": topic_dirs,
        "discovered_files": discovered_files,
        "shelves": shelves,
        "current_shelf_for_id": current_shelf_for_id,
        "tree": tree,
        "listings": listings,
    }


def build_session_plan(state: dict, spec: PlanSpec) -> SessionPlan:
    plan = SessionPlan()
    topic_dirs = state["topic_dirs"]
    discovered_files = state["discovered_files"]
    shelves = state["shelves"]
    current_shelf_for_id = state["current_shelf_for_id"]

    for category in spec.category_names:
        if category not in topic_dirs:
            plan.missing_storage_folders.append(category)

    storage_moves: dict[str, list[str]] = defaultdict(list)
    for file_name, target_path in spec.physical_target_by_name.items():
        current_path = discovered_files.get(file_name)
        if not current_path:
            plan.warnings.append(f"missing physical file: {file_name}")
            continue
        if current_path != target_path:
            storage_moves[str(PurePosixPath(target_path).parent)].append(current_path)
    plan.storage_moves = dict(storage_moves)

    for category in spec.category_names:
        if category not in shelves:
            plan.missing_shelves.append(category)

    shelf_moves: dict[str, list[str]] = defaultdict(list)
    for item_id, category in spec.shelf_target_by_id.items():
        current_shelf = current_shelf_for_id.get(item_id)
        if current_shelf is None and item_id not in current_shelf_for_id:
            plan.warnings.append(f"missing library item: {item_id}")
            continue
        if current_shelf != category:
            shelf_moves[category].append(item_id)
    plan.shelf_moves = dict(shelf_moves)
    return plan


def print_plan(plan: SessionPlan) -> None:
    info("Session plan")
    typer.echo(
        f"  missing storage folders: {', '.join(plan.missing_storage_folders) or 'none'}"
    )
    if plan.storage_moves:
        typer.echo("  storage moves:")
        for parent, items in plan.storage_moves.items():
            typer.echo(f"    {parent}: {len(items)}")
    else:
        typer.echo("  storage moves: none")
    typer.echo(f"  missing shelves: {', '.join(plan.missing_shelves) or 'none'}")
    if plan.shelf_moves:
        typer.echo("  shelf moves:")
        for shelf, items in plan.shelf_moves.items():
            typer.echo(f"    {shelf}: {len(items)}")
    else:
        typer.echo("  shelf moves: none")
    if plan.warnings:
        warning("  warnings:")
        for warning in plan.warnings:
            typer.echo(f"    {warning}")
    else:
        typer.echo("  warnings: none")


async def run_inventory(client: BooxClient, spec: PlanSpec | None) -> int:
    if spec is None:
        books_root = await client.storage_list(DEFAULT_STORAGE_ROOT)
        download_root = await client.storage_list(DEFAULT_SCAN_DIRS[1])
        tree = await client.library_tree()
        print("Storage")
        print("  Books root:")
        for item in books_root:
            print(f"    {'DIR' if item['dir'] else 'FILE'} {item['path']}")
        print("  Download root:")
        for item in download_root:
            print(f"    {'DIR' if item['dir'] else 'FILE'} {item['path']}")
        print("Library shelves")
        for child in tree.get("children", []):
            print(f"  {child['library']['name']}: {child['bookCount']}")
        return 0

    state = await gather_state(client, spec)
    print("Storage")
    print("  Books root:")
    for item in state["books_root"]:
        print(f"    {'DIR' if item['dir'] else 'FILE'} {item['path']}")
    if state["download_root"]:
        print("  Download root:")
        for item in state["download_root"]:
            print(f"    {'DIR' if item['dir'] else 'FILE'} {item['path']}")
    print("Library shelves")
    for child in state["tree"].get("children", []):
        print(f"  {child['library']['name']}: {child['bookCount']}")
    return 0


async def run_organize(
    client: BooxClient, spec: PlanSpec, apply_changes: bool, settle_seconds: int
) -> int:
    initial_state = await gather_state(client, spec)
    plan = build_session_plan(initial_state, spec)
    print_plan(plan)

    if not apply_changes:
        return 0

    if plan.warnings:
        print("Refusing to apply with unresolved warnings.", file=sys.stderr)
        return 1

    for category in plan.missing_storage_folders:
        result = await client.create_storage_folder(spec.storage_root, category)
        print(f"created storage folder {category}: {result.get('successful', False)}")

    for parent, selected in plan.storage_moves.items():
        result = await client.move_storage_files(parent, selected, force=False)
        print(
            f"moved {len(selected)} file(s) into {parent}: {result.get('successful', False)}"
        )

    if plan.missing_storage_folders or plan.storage_moves:
        time.sleep(settle_seconds)

    refreshed_state = await gather_state(client, spec)
    refreshed_plan = build_session_plan(refreshed_state, spec)

    for category in refreshed_plan.missing_shelves:
        result = await client.create_shelf(category, None)
        print(f"created shelf {category}: {result.get('successful', False)}")

    if refreshed_plan.missing_shelves:
        refreshed_state = await gather_state(client, spec)
        refreshed_plan = build_session_plan(refreshed_state, spec)

    for category, item_ids in refreshed_plan.shelf_moves.items():
        shelf_id = refreshed_state["shelves"].get(category)
        if not shelf_id:
            print(f"missing shelf id for {category}", file=sys.stderr)
            return 1
        result = await client.move_library_files(
            category, shelf_id, item_ids, None, force=False
        )
        print(
            f"moved {len(item_ids)} library item(s) into {category}: {result.get('successful', False)}"
        )

    final_state = await gather_state(client, spec)
    final_plan = build_session_plan(final_state, spec)
    print("Final validation")
    print_plan(final_plan)
    return 0 if not final_plan.has_changes() and not final_plan.warnings else 1


async def run_validate(client: BooxClient, spec: PlanSpec) -> int:
    state = await gather_state(client, spec)
    plan = build_session_plan(state, spec)
    print_plan(plan)
    if plan.has_changes() or plan.warnings:
        return 1
    print("Validation passed.")
    return 0


def parent_dir(path: str) -> str:
    return str(PurePosixPath(path).parent)


def plan_directory_steps(storage_root: str, target_dir: str) -> list[tuple[str, str]]:
    root = PurePosixPath(storage_root)
    directory = PurePosixPath(target_dir)
    try:
        relative_parts = directory.relative_to(root).parts
    except ValueError as exc:
        raise ValueError(
            f"target dir {target_dir} is outside storage root {storage_root}"
        ) from exc

    current = str(root)
    steps: list[tuple[str, str]] = []
    for part in relative_parts:
        steps.append((current, part))
        current = str(PurePosixPath(current) / part)
    return steps


def load_manifest(manifest_path: str) -> dict:
    return load_structured_file(manifest_path)


def cache_file_path(cache_dir: str, entry: dict) -> Path:
    identifier = str(entry.get("resolved_id") or entry.get("arxiv_id") or "unknown")
    safe_identifier = re.sub(r"[^A-Za-z0-9._-]+", "-", identifier)
    return Path(cache_dir) / f"{safe_identifier}.pdf"


async def download_binary(url: str) -> bytes:
    def _download() -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "arxiv-radar/0.1.0"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()

    return await asyncio.to_thread(_download)


async def run_sync_staged_manifest(
    client: BooxClient,
    manifest_path: str,
    apply_changes: bool,
    settle_seconds: int,
    db_path: str | None = None,
) -> int:
    manifest = load_manifest(manifest_path)
    if not isinstance(manifest.get("entries"), list):
        raise ValueError("manifest must contain an 'entries' list")
    spec = contract_to_spec(manifest)
    entries = [entry for entry in manifest["entries"] if isinstance(entry, dict)]

    state = await gather_state(client, spec)
    existing_paths = set(state["discovered_files"].values())
    missing_entries = [
        entry
        for entry in entries
        if entry.get("local_pdf_path")
        and entry.get("target_path")
        and entry["target_path"] not in existing_paths
    ]

    already_present_ids = [
        e.get("arxiv_id", "") for e in entries
        if e.get("target_path") and e["target_path"] in existing_paths and e.get("arxiv_id")
    ]

    info(f"manifest entries={len(entries)} missing_uploads={len(missing_entries)}")
    for entry in missing_entries:
        typer.echo(f"  {entry['target_path']}")

    if not apply_changes:
        if db_path:
            _record_sync_to_db(
                db_path, client.host, manifest, False,
                skipped_ids=already_present_ids,
            )
        return 0

    ensured_dirs = {
        spec.storage_root,
        *state["topic_dirs"].values(),
        *state["listings"].keys(),
    }
    needed_dirs = sorted(
        {parent_dir(entry["target_path"]) for entry in missing_entries}
    )
    for directory in needed_dirs:
        for parent, name in plan_directory_steps(spec.storage_root, directory):
            next_dir = str(PurePosixPath(parent) / name)
            if next_dir in ensured_dirs:
                continue
            try:
                await client.create_storage_folder(parent, name)
            except RuntimeError as exc:
                lowered = str(exc).lower()
                if (
                    "exist" not in lowered
                    and "duplicate" not in lowered
                    and "same name" not in lowered
                ):
                    raise
            ensured_dirs.add(next_dir)

    uploaded_ids: list[str] = []
    failed_ids: list[tuple[str, str]] = []
    for entry in missing_entries:
        target_path = entry["target_path"]
        file_name = PurePosixPath(target_path).name
        directory = parent_dir(target_path)
        local_pdf_path = entry.get("local_pdf_path")
        if not local_pdf_path:
            raise ValueError(f"missing local_pdf_path for {target_path}")
        local_path = Path(local_pdf_path)
        if not local_path.exists() or local_path.stat().st_size <= 0:
            raise ValueError(
                f"staged file is missing for {target_path}: {local_pdf_path}"
            )
        info(f"using staged file {local_path.name}")
        content = local_path.read_bytes()
        info(f"uploading {file_name}")
        try:
            await client.upload_storage_file(directory, file_name, content)
            arxiv_id = entry.get("arxiv_id", "")
            if arxiv_id:
                uploaded_ids.append(arxiv_id)
        except Exception as exc:
            arxiv_id = entry.get("arxiv_id", "")
            if arxiv_id:
                failed_ids.append((arxiv_id, str(exc)))
            raise

    retries = 3
    for attempt in range(retries):
        await asyncio.sleep(settle_seconds)
        state = await gather_state(client, spec)
        plan = build_session_plan(state, spec)
        unresolved_library = [
            warning_text
            for warning_text in plan.warnings
            if warning_text.startswith("missing library item:")
        ]
        if not unresolved_library:
            break
        if attempt == retries - 1:
            break
        info("waiting for library index to catch up")

    if db_path:
        _record_sync_to_db(
            db_path, client.host, manifest, True,
            uploaded_ids=uploaded_ids,
            skipped_ids=already_present_ids,
            failed_ids=failed_ids,
        )

    sync_code = await run_organize(client, spec, True, settle_seconds)
    if sync_code != 0:
        return sync_code
    return await run_validate(client, spec)


async def run_arxiv_ingest(
    input_path: str | None,
    ids: list[str],
    category: str,
    storage_root: str,
    output_path: str | None,
) -> int:
    arxiv_ids = load_arxiv_ids(input_path, ids)
    manifest = build_arxiv_manifest(arxiv_ids, category, storage_root)
    write_json_output(manifest, output_path)
    return 0


async def run_research_radar(config_path: str, output_dir: str | None = None) -> int:
    spec = load_radar_config(config_path)
    resolved_output_dir = output_dir or spec.output_dir
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "lookback_days": spec.lookback_days,
        "storage_root": spec.storage_root,
        "categories": [],
    }
    for category in spec.categories:
        info(f"surveying {category.name}")
        report["categories"].append(build_radar_category_report(spec, category))

    json_path, md_path = write_radar_outputs(report, resolved_output_dir)
    success(f"wrote {json_path}")
    success(f"wrote {md_path}")
    for category in report["categories"]:
        typer.echo(
            f"{category['name']}: new={len(category['recent'])} cited={len(category['highly_cited'])}"
        )
    return 0


async def run_huggingface_papers_radar(
    *,
    date: str | None,
    output_dir: str,
    storage_root: str,
    category_name: str,
    target_path: str,
    limit: int | None = None,
    min_upvotes: int | None = None,
) -> int:
    info(f"fetching Hugging Face papers for {date or 'latest'}")
    report = build_huggingface_papers_report(
        date=date,
        storage_root=storage_root,
        category_name=category_name,
        target_path=target_path,
        limit=limit,
        min_upvotes=min_upvotes,
    )
    json_path, md_path = write_huggingface_papers_radar_outputs(report, output_dir)
    success(f"wrote {json_path}")
    success(f"wrote {md_path}")
    category = report["categories"][0]
    typer.echo(
        f"{category['name']}: papers={len(category['recent'])} min_upvotes={min_upvotes if min_upvotes is not None else 0}"
    )
    return 0


async def run_radar_tui(
    config_path: str,
    radar_json: str | None = None,
    output_path: str | None = None,
) -> int:
    spec = load_radar_config(config_path)
    report_path = (
        Path(radar_json) if radar_json else latest_radar_report_path(spec.output_dir)
    )
    report = load_radar_report(str(report_path))
    resolved_output = (
        Path(output_path) if output_path else default_curated_output_path(report_path)
    )
    app = RadarCuratorApp(report, report_path, resolved_output)
    await app.run_async()
    if app.exported:
        success(f"wrote {resolved_output}")
    else:
        warning("radar curation exited without export")
    return 0


def build_client(runtime: RuntimeInputs) -> BooxClient:
    assert runtime.host is not None
    client = BooxClient(runtime.host, token=runtime.token, password=runtime.password)
    return client


def _record_export_to_db(
    db_path: str,
    manifest: dict,
    *,
    section: str = "",
    categories: list[str] | None = None,
    top: int | None = None,
    min_citations: int | None = None,
    max_citations: int | None = None,
    since: str | None = None,
    lookback_days: int | None = None,
) -> None:
    from radar_db import init_db, get_db, ingest_manifest as db_ingest_manifest

    init_db(db_path)
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(json.dumps(manifest, indent=2, ensure_ascii=False))
        tmp_path = tmp.name
    try:
        with get_db(db_path) as conn:
            db_ingest_manifest(
                conn,
                tmp_path,
                source="export",
                section=section,
                categories=categories,
                top_n=top,
                min_citations=min_citations,
                max_citations=max_citations,
                since_date=since,
                lookback_days=lookback_days,
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _record_sync_to_db(
    db_path: str,
    host: str,
    manifest: dict,
    apply_changes: bool,
    *,
    uploaded_ids: list[str] | None = None,
    skipped_ids: list[str] | None = None,
    failed_ids: list[tuple[str, str]] | None = None,
) -> None:
    from radar_db import (
        init_db, get_db, register_device, create_sync_session,
        finish_sync_session, record_sync_outcome, mark_synced, ensure_sync_states,
    )

    init_db(db_path)
    entries = [e for e in manifest.get("entries", []) if isinstance(e, dict)]
    with get_db(db_path) as conn:
        device = register_device(conn, host)
        session_id = create_sync_session(conn, device.id)

        arxiv_targets = [(e.get("arxiv_id", ""), e.get("target_path", "")) for e in entries if e.get("arxiv_id")]
        ensure_sync_states(conn, device.id, arxiv_targets)

        uploaded = uploaded_ids or []
        skipped = skipped_ids or []
        failed = failed_ids or []

        for arxiv_id in uploaded:
            record_sync_outcome(conn, session_id, arxiv_id, "uploaded")
            mark_synced(conn, device.id, arxiv_id, "synced")
        for arxiv_id in skipped:
            record_sync_outcome(conn, session_id, arxiv_id, "already_present")
        for arxiv_id, err in failed:
            record_sync_outcome(conn, session_id, arxiv_id, "failed", detail=err)
            mark_synced(conn, device.id, arxiv_id, "failed", error_msg=err)

        finish_sync_session(
            conn, session_id,
            papers_total=len(entries),
            papers_synced=len(uploaded),
            papers_failed=len(failed),
            papers_skipped=len(skipped),
            applied=apply_changes,
        )


async def run_radar_workflow(
    config_path: str,
    output_dir: str | None = None,
    radar_json: str | None = None,
    curated_output: str | None = None,
    refresh: bool = False,
) -> int:
    spec = load_radar_config(config_path)
    resolved_output_dir = output_dir or spec.output_dir
    selected_report = Path(radar_json) if radar_json else None

    if refresh:
        await run_research_radar(config_path, resolved_output_dir)
        if selected_report is None:
            selected_report = latest_radar_report_path(resolved_output_dir)

    if selected_report is None:
        try:
            selected_report = latest_radar_report_path(resolved_output_dir)
        except ValueError:
            info("no existing radar report found; generating one first")
            await run_research_radar(config_path, resolved_output_dir)
            selected_report = latest_radar_report_path(resolved_output_dir)

    return await run_radar_tui(config_path, str(selected_report), curated_output)
