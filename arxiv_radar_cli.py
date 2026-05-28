from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess

import typer

from booxdrop_cli import (
    DEFAULT_CURATED_MANIFEST_PATH,
    DEFAULT_PDF_CACHE_DIR,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_RADAR_CONFIG,
    DEFAULT_REPORT_BUILD_DIR,
    DEFAULT_REPORT_MARKDOWN_CACHE_DIR,
    DEFAULT_REPORT_PDF_PATH,
    DEFAULT_REPORT_SUMMARY_CACHE_DIR,
    DEFAULT_REPORT_TEX_PATH,
    DEFAULT_STAGED_MANIFEST_PATH,
    build_periodical,
    build_summary_report,
    export_radar_manifest,
    prepare_manifest,
    radar_export_summary,
    latest_radar_report_path,
    load_manifest,
    load_radar_config,
    prime_cache,
    run_arxiv_ingest,
    run_huggingface_papers_radar,
    run_radar_tui,
    run_radar_workflow,
    run_research_radar,
    stage_manifest,
    summarize_cache,
    summarize_manifest,
)
from radar_db import (
    DEFAULT_DB_PATH,
    EnrichmentRecord,
    ExtractionRecord,
    PeriodicalBuild,
    ReferenceEdge,
    init_db,
    get_db,
    db_status,
    seed_from_json_artifacts,
    register_device,
    reconcile_device,
    get_pending_sync,
    get_synced_papers,
    build_staged_manifest_from_db,
    ingest_radar_report as db_ingest_report,
    ingest_manifest as db_ingest_manifest,
    create_sync_session,
    finish_sync_session,
    record_sync_outcome,
    mark_synced,
    ensure_sync_states,
    upsert_paper,
    entry_to_paper,
    record_retrieval,
    RetrievalRecord,
    upsert_extraction,
    get_extraction,
    pending_extractions,
    ExtractionRecord,
    upsert_enrichment,
    record_reference_edges,
    start_periodical_build,
    finish_periodical_build,
    record_periodical_paper,
)
from paperflow_refresh import (
    DEFAULT_REFRESH_LOCK_PATH,
    DEFAULT_REFRESH_LOG_DIR,
    DEFAULT_REFRESH_STATUS_PATH,
    run_refresh,
)


def _format_filter_summary(
    *,
    section: str,
    categories: list[str],
    exclude_categories: list[str],
    top: int | None,
    min_citations: int | None,
    max_citations: int | None,
    since: str | None,
    lookback_days: int | None,
) -> str:
    parts = [f"section={section}"]
    if categories:
        parts.append(f"categories={','.join(categories)}")
    if exclude_categories:
        parts.append(f"exclude_categories={','.join(exclude_categories)}")
    if top is not None:
        parts.append(f"top={top}")
    if min_citations is not None:
        parts.append(f"min_citations={min_citations}")
    if max_citations is not None:
        parts.append(f"max_citations={max_citations}")
    if since:
        parts.append(f"since={since}")
    if lookback_days is not None:
        parts.append(f"lookback_days={lookback_days}")
    return ", ".join(parts)


def _warn_if_export_empty(
    *,
    report_path: str,
    section: str,
    categories: list[str],
    exclude_categories: list[str],
    top: int | None,
    min_citations: int | None,
    max_citations: int | None,
    since: str | None,
    lookback_days: int | None,
) -> None:
    summary = radar_export_summary(
        report_path,
        section=section,
        categories=categories,
        exclude_categories=exclude_categories,
        top=top,
        min_citations=min_citations,
        max_citations=max_citations,
        since=since,
        lookback_days=lookback_days,
    )
    if summary["selected_count"] != 0:
        return
    typer.secho(
        "Current default export policy yields zero papers.",
        fg=typer.colors.YELLOW,
        err=True,
    )


def _boox_sync_executable() -> str:
    found = shutil.which("boox-sync")
    if found:
        return found
    local = Path(__file__).with_name("boox-sync")
    if local.exists():
        return str(local)
    raise RuntimeError("could not locate boox-sync executable")
    typer.secho(
        _format_filter_summary(
            section=section,
            categories=categories,
            exclude_categories=exclude_categories,
            top=top,
            min_citations=min_citations,
            max_citations=max_citations,
            since=since,
            lookback_days=lookback_days,
        ),
        fg=typer.colors.YELLOW,
        err=True,
    )


app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Host-independent arXiv radar workflow",
)


@app.callback(invoke_without_command=True)
def arxiv_radar_callback(
    ctx: typer.Context,
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to an arXiv radar YAML config",
    ),
    output_dir: str | None = typer.Option(
        None,
        help="Override the output directory from the config",
    ),
    radar_json: str | None = typer.Option(
        None,
        help="Specific radar JSON report to curate; defaults to the latest report",
    ),
    output: str | None = typer.Option(
        None,
        help=f"Curated manifest output path; defaults to {DEFAULT_CURATED_MANIFEST_PATH}",
    ),
    refresh: bool = typer.Option(
        False,
        help="Generate a fresh radar report before opening the TUI",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    spec = load_radar_config(config)
    try:
        report_path = radar_json or str(
            latest_radar_report_path(output_dir or spec.output_dir)
        )
        _warn_if_export_empty(
            report_path=report_path,
            section=spec.export_section,
            categories=spec.export_categories,
            exclude_categories=spec.export_exclude_categories,
            top=spec.export_top,
            min_citations=spec.export_min_citations,
            max_citations=spec.export_max_citations,
            since=spec.export_since,
            lookback_days=spec.export_lookback_days,
        )
    except ValueError:
        pass
    raise typer.Exit(
        asyncio.run(run_radar_workflow(config, output_dir, radar_json, output, refresh))
    )


@app.command("ingest")
def arxiv_ingest_command(
    ids: list[str] = typer.Argument(None, help="arXiv ids or arXiv URLs"),
    input: str | None = typer.Option(
        None, help="Path to a text file containing arXiv ids or URLs"
    ),
    category: str = typer.Option(
        "AI", help="Target category name for suggested output paths"
    ),
    storage_root: str = typer.Option(
        "/storage/emulated/0/Books",
        help="Storage root used to build suggested target paths",
    ),
    output: str | None = typer.Option(
        None, help="Write the manifest JSON to a file instead of stdout"
    ),
) -> None:
    raise typer.Exit(
        asyncio.run(run_arxiv_ingest(input, ids or [], category, storage_root, output))
    )


@app.command("generate")
def generate_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to an arXiv radar YAML config",
    ),
    output_dir: str | None = typer.Option(
        None, help="Override the output directory from the config"
    ),
) -> None:
    spec = load_radar_config(config)
    resolved_output_dir = output_dir or spec.output_dir
    raise typer.Exit(asyncio.run(run_research_radar(config, resolved_output_dir)))


@app.command("refresh")
def refresh_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to the stable local arXiv radar YAML config",
    ),
    output_dir: str | None = typer.Option(
        None, help="Override the output directory from the config"
    ),
    source: list[str] = typer.Option(
        None, "--source", help="Refresh only this source; repeat for multiple sources"
    ),
    skip_source: list[str] = typer.Option(
        None, "--skip-source", help="Skip this source; repeat for multiple sources"
    ),
    offline: bool = typer.Option(
        False, help="Build refresh outputs from local DB/cache state only"
    ),
    update_only: bool = typer.Option(
        False,
        help="Cron-safe source refresh only; never opens TUI, syncs, downloads PDFs, or builds reports",
    ),
    fail_on_lock: bool = typer.Option(
        False, help="Exit non-zero when another refresh already owns the lock"
    ),
    lock_path: str = typer.Option(
        DEFAULT_REFRESH_LOCK_PATH, help="Refresh lock file path"
    ),
    status_path: str = typer.Option(
        DEFAULT_REFRESH_STATUS_PATH, help="Structured refresh status JSON path"
    ),
    log_dir: str = typer.Option(
        DEFAULT_REFRESH_LOG_DIR, help="Directory for structured refresh logs"
    ),
    db: str = typer.Option(
        DEFAULT_DB_PATH, help="SQLite database path for refresh state"
    ),
) -> None:
    result = run_refresh(
        config_path=config,
        output_dir=output_dir,
        db_path=db,
        requested_sources=source or [],
        skipped_sources=skip_source or [],
        offline=offline,
        update_only=update_only,
        fail_on_lock=fail_on_lock,
        lock_path=lock_path,
        status_path=status_path,
        log_dir=log_dir,
    )
    if result.locked:
        typer.echo(f"refresh locked; status={result.status_path}")
    else:
        typer.echo(f"report={result.report_path}")
        typer.echo(f"status={result.status_path}")
        for item in result.sources:
            typer.echo(
                f"{item.get('source')}: {item.get('status')} count={item.get('item_count', 0)}"
            )
    raise typer.Exit(result.exit_code)


@app.command("hf-papers")
def huggingface_papers_command(
    date: str | None = typer.Option(
        None,
        help="Hugging Face daily papers date in YYYY-MM-DD format; defaults to latest",
    ),
    output_dir: str = typer.Option(
        "hf-papers-output",
        help="Output directory for Hugging Face papers radar artifacts",
    ),
    storage_root: str = typer.Option(
        "/storage/emulated/0/Books",
        help="Storage root used to build suggested target paths",
    ),
    category_name: str = typer.Option(
        "AI",
        help="Report category name used by export filters",
    ),
    target_path: str = typer.Option(
        "AI/Hugging Face Papers",
        help="Target path under the storage root for selected papers",
    ),
    limit: int | None = typer.Option(
        None,
        help="Optional maximum number of papers to keep after ranking by HF upvotes",
    ),
    min_upvotes: int | None = typer.Option(
        None,
        help="Optional minimum Hugging Face upvote filter",
    ),
) -> None:
    raise typer.Exit(
        asyncio.run(
            run_huggingface_papers_radar(
                date=date,
                output_dir=output_dir,
                storage_root=storage_root,
                category_name=category_name,
                target_path=target_path,
                limit=limit,
                min_upvotes=min_upvotes,
            )
        )
    )


@app.command("prepare")
def prepare_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to an arXiv radar YAML config",
    ),
    radar_json: str | None = typer.Option(
        None,
        help="Specific radar JSON report to prepare from; defaults to the latest report from the config output dir",
    ),
    section: str | None = typer.Option(
        None,
        help="Which report section to prepare: highly_cited, recent, or all",
    ),
    category: list[str] = typer.Option(
        None, help="Optional category filter; repeat for multiple categories"
    ),
    exclude_category: list[str] = typer.Option(
        None, help="Optional category exclusion; repeat for multiple categories"
    ),
    top: int | None = typer.Option(
        None, help="Optional top-N limit after filtering, sorted by citations"
    ),
    min_citations: int | None = typer.Option(
        None, help="Optional minimum citation count filter"
    ),
    max_citations: int | None = typer.Option(
        None, help="Optional maximum citation count filter"
    ),
    since: str | None = typer.Option(
        None, help="Optional published-since filter in ISO date format, e.g. 2026-04-01"
    ),
    lookback_days: int | None = typer.Option(
        None,
        help="Optional lookback window in days, relative to the report generation time",
    ),
    manifest_output: str | None = typer.Option(
        None, help=f"Prepared manifest path; defaults to {DEFAULT_MANIFEST_PATH}"
    ),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR, help="Local PDF cache directory"
    ),
    staged_output: str | None = typer.Option(
        None,
        help=f"Staged manifest output path; defaults to {DEFAULT_STAGED_MANIFEST_PATH}",
    ),
    db: str | None = typer.Option(
        None, help="SQLite database path for tracking export state"
    ),
    curated: str | None = typer.Option(
        None,
        help=f"Curated manifest to use instead of re-exporting from the radar report; defaults to {DEFAULT_CURATED_MANIFEST_PATH} if it exists",
    ),
) -> None:
    spec = load_radar_config(config)
    report_path = radar_json or str(latest_radar_report_path(spec.output_dir))
    resolved_curated = curated or (
        DEFAULT_CURATED_MANIFEST_PATH
        if Path(DEFAULT_CURATED_MANIFEST_PATH).exists()
        else None
    )
    result = asyncio.run(
        prepare_manifest(
            report_path,
            section=section or spec.export_section,
            categories=(category or []) or spec.export_categories,
            exclude_categories=(exclude_category or [])
            or spec.export_exclude_categories,
            top=top if top is not None else spec.export_top,
            min_citations=min_citations
            if min_citations is not None
            else spec.export_min_citations,
            max_citations=max_citations
            if max_citations is not None
            else spec.export_max_citations,
            since=since or spec.export_since,
            lookback_days=lookback_days
            if lookback_days is not None
            else spec.export_lookback_days,
            manifest_output=manifest_output,
            cache_dir=cache_dir,
            staged_output=staged_output,
            db_path=db,
            curated_path=resolved_curated,
        )
    )
    typer.echo(f"manifest={result['manifest_path']}")
    typer.echo(f"staged={result['staged_path']}")
    typer.echo(f"selected_count={result['manifest_summary']['selected_count']}")
    typer.echo(f"cached_entries={result['cache_summary']['cached_entries']}")
    typer.echo(f"missing_entries={result['cache_summary']['missing_entries']}")


@app.command("report")
def report_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to the stable local arXiv radar YAML config",
    ),
    manifest: str = typer.Option(
        DEFAULT_MANIFEST_PATH,
        help=f"Manifest path; defaults to {DEFAULT_MANIFEST_PATH}",
    ),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR,
        help="Local PDF cache directory",
    ),
    model: str | None = typer.Option(
        None,
        help="Override the configured opencode model",
    ),
    variant: str | None = typer.Option(
        None,
        help="Override the configured opencode model variant",
    ),
    max_papers: int | None = typer.Option(
        None,
        help="Optional paper limit for report generation",
    ),
    refresh_summaries: bool = typer.Option(
        False,
        help="Regenerate cached LLM summaries instead of reusing them",
    ),
    summary_cache_dir: str | None = typer.Option(
        None,
        help=f"Override summary cache dir; defaults to {DEFAULT_REPORT_SUMMARY_CACHE_DIR}",
    ),
    markdown_cache_dir: str | None = typer.Option(
        None,
        help=f"Override markdown cache dir; defaults to {DEFAULT_REPORT_MARKDOWN_CACHE_DIR}",
    ),
    output_tex: str | None = typer.Option(
        None,
        help=f"Override TeX output path; defaults to {DEFAULT_REPORT_TEX_PATH}",
    ),
    output_pdf: str | None = typer.Option(
        None,
        help=f"Override PDF output path; defaults to {DEFAULT_REPORT_PDF_PATH}",
    ),
    build_dir: str | None = typer.Option(
        None,
        help=f"Override TeX build dir; defaults to {DEFAULT_REPORT_BUILD_DIR}",
    ),
) -> None:
    spec = load_radar_config(config)
    result = asyncio.run(
        build_summary_report(
            manifest,
            cache_dir=cache_dir,
            model=model or spec.report_model,
            variant=variant if variant is not None else spec.report_variant,
            summary_cache_dir=summary_cache_dir or spec.report_summary_cache_dir,
            markdown_cache_dir=markdown_cache_dir or spec.report_markdown_cache_dir,
            prompt_version=spec.report_prompt_version,
            title=spec.report_title,
            max_papers=max_papers if max_papers is not None else spec.report_max_papers,
            output_tex=output_tex or spec.report_output_tex,
            output_pdf=output_pdf or spec.report_output_pdf,
            build_dir=build_dir or spec.report_build_dir,
            refresh_summaries=refresh_summaries,
        )
    )
    typer.echo(f"manifest={result['manifest_path']}")
    typer.echo(f"output_tex={result['output_tex']}")
    typer.echo(f"output_pdf={result['output_pdf']}")
    typer.echo(f"summary_index={result['summary_index']}")
    typer.echo(f"paper_count={result['paper_count']}")
    typer.echo(f"model={result['model']}")


@app.command("periodical")
def periodical_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to the stable local arXiv radar YAML config",
    ),
    manifest: str = typer.Option(
        DEFAULT_MANIFEST_PATH,
        help=f"Manifest path; defaults to {DEFAULT_MANIFEST_PATH}",
    ),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR,
        help="Local PDF cache directory",
    ),
    model: str | None = typer.Option(
        None,
        help="Override the configured opencode model",
    ),
    variant: str | None = typer.Option(
        None,
        help="Override the configured opencode model variant",
    ),
    max_papers: int | None = typer.Option(
        None,
        help="Optional paper limit for periodical generation",
    ),
    refresh_summaries: bool = typer.Option(
        False,
        help="Regenerate cached LLM summaries instead of reusing them",
    ),
    summary_cache_dir: str | None = typer.Option(
        None,
        help=f"Override summary cache dir; defaults to {DEFAULT_REPORT_SUMMARY_CACHE_DIR}",
    ),
    markdown_cache_dir: str | None = typer.Option(
        None,
        help=f"Override markdown cache dir; defaults to {DEFAULT_REPORT_MARKDOWN_CACHE_DIR}",
    ),
    periodical_dir: str = typer.Option(
        "tex/research-radar",
        help="Output directory for the periodical TeX project",
    ),
    reference_depth: int | None = typer.Option(
        None,
        help="Citation traversal depth (0=off, 1=direct refs, 2=refs-of-refs)",
    ),
    max_references_per_paper: int | None = typer.Option(
        None,
        help="Max foundational references to follow per paper",
    ),
    min_reference_citations: int | None = typer.Option(
        None,
        help="Minimum citation count for a reference to be included",
    ),
    db: str | None = typer.Option(
        DEFAULT_DB_PATH, help="SQLite database path for tracking"
    ),
) -> None:
    spec = load_radar_config(config)
    result = asyncio.run(
        build_periodical(
            manifest,
            cache_dir=cache_dir,
            model=model or spec.report_model,
            variant=variant if variant is not None else spec.report_variant,
            summary_cache_dir=summary_cache_dir or spec.report_summary_cache_dir,
            markdown_cache_dir=markdown_cache_dir or spec.report_markdown_cache_dir,
            prompt_version=spec.report_prompt_version,
            title=spec.report_title,
            max_papers=max_papers if max_papers is not None else spec.report_max_papers,
            periodical_dir=periodical_dir,
            refresh_summaries=refresh_summaries,
            reference_depth=reference_depth if reference_depth is not None else spec.reference_depth,
            max_references_per_paper=max_references_per_paper if max_references_per_paper is not None else spec.max_references_per_paper,
            min_reference_citations=min_reference_citations if min_reference_citations is not None else spec.min_reference_citations,
            db_path=db,
        )
    )
    typer.echo(f"periodical_dir={result['periodical_dir']}")
    typer.echo(f"output_pdf={result['output_pdf']}")
    typer.echo(f"paper_count={result['paper_count']}")
    typer.echo(f"foundational_count={result['foundational_count']}")
    typer.echo(f"chapter_count={result['chapter_count']}")
    typer.echo(f"model={result['model']}")


@app.command("deliver")
def deliver_command(
    host: str = typer.Option(..., help="BOOX Drop host URL"),
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to the stable local arXiv radar YAML config",
    ),
    radar_json: str | None = typer.Option(
        None,
        help="Specific radar JSON report to prepare from; defaults to the latest report from the config output dir",
    ),
    section: str | None = typer.Option(
        None, help="Which report section to prepare: highly_cited, recent, or all"
    ),
    category: list[str] = typer.Option(
        None, help="Optional category filter; repeat for multiple categories"
    ),
    exclude_category: list[str] = typer.Option(
        None, help="Optional category exclusion; repeat for multiple categories"
    ),
    top: int | None = typer.Option(
        None, help="Optional top-N limit after filtering, sorted by citations"
    ),
    min_citations: int | None = typer.Option(
        None, help="Optional minimum citation count filter"
    ),
    max_citations: int | None = typer.Option(
        None, help="Optional maximum citation count filter"
    ),
    since: str | None = typer.Option(
        None, help="Optional published-since filter in ISO date format"
    ),
    lookback_days: int | None = typer.Option(
        None,
        help="Optional lookback window in days, relative to the report generation time",
    ),
    apply: bool = typer.Option(
        False, help="Apply the staged sync to BOOX instead of dry-run"
    ),
    curated: str | None = typer.Option(
        None,
        help=f"Curated manifest to use instead of re-exporting from the radar report; defaults to {DEFAULT_CURATED_MANIFEST_PATH} if it exists",
    ),
) -> None:
    spec = load_radar_config(config)
    report_path = radar_json or str(latest_radar_report_path(spec.output_dir))
    resolved_curated = curated or (
        DEFAULT_CURATED_MANIFEST_PATH
        if Path(DEFAULT_CURATED_MANIFEST_PATH).exists()
        else None
    )
    prepared = asyncio.run(
        prepare_manifest(
            report_path,
            section=section or spec.export_section,
            categories=(category or []) or spec.export_categories,
            exclude_categories=(exclude_category or [])
            or spec.export_exclude_categories,
            top=top if top is not None else spec.export_top,
            min_citations=min_citations
            if min_citations is not None
            else spec.export_min_citations,
            max_citations=max_citations
            if max_citations is not None
            else spec.export_max_citations,
            since=since or spec.export_since,
            lookback_days=lookback_days
            if lookback_days is not None
            else spec.export_lookback_days,
            manifest_output=DEFAULT_MANIFEST_PATH,
            cache_dir=DEFAULT_PDF_CACHE_DIR,
            staged_output=DEFAULT_STAGED_MANIFEST_PATH,
            curated_path=resolved_curated,
        )
    )
    typer.echo(f"prepared_manifest={prepared['manifest_path']}")
    typer.echo(f"prepared_staged={prepared['staged_path']}")
    sync_cmd = [
        _boox_sync_executable(),
        "sync-manifest",
        "--host",
        host,
        "--manifest",
        prepared["staged_path"],
    ]
    if apply:
        sync_cmd.append("--apply")
    result = subprocess.run(sync_cmd, cwd="/Users/jrepp/dev/boox-org")
    raise typer.Exit(result.returncode)


@app.command("export")
def export_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to an arXiv radar YAML config",
    ),
    radar_json: str | None = typer.Option(
        None,
        help="Specific radar JSON report to export from; defaults to the latest report from the config output dir",
    ),
    section: str | None = typer.Option(
        None,
        help="Which report section to export: highly_cited, recent, or all",
    ),
    category: list[str] = typer.Option(
        None,
        help="Optional category filter; repeat for multiple categories",
    ),
    exclude_category: list[str] = typer.Option(
        None,
        help="Optional category exclusion; repeat for multiple categories",
    ),
    top: int | None = typer.Option(
        None,
        help="Optional top-N limit after filtering, sorted by citations",
    ),
    min_citations: int | None = typer.Option(
        None,
        help="Optional minimum citation count filter",
    ),
    max_citations: int | None = typer.Option(
        None,
        help="Optional maximum citation count filter",
    ),
    since: str | None = typer.Option(
        None,
        help="Optional published-since filter in ISO date format, e.g. 2026-04-01",
    ),
    lookback_days: int | None = typer.Option(
        None,
        help="Optional lookback window in days, relative to the report generation time",
    ),
    output: str | None = typer.Option(
        None,
        help=f"Manifest output path; defaults to {DEFAULT_MANIFEST_PATH}",
    ),
    db: str | None = typer.Option(
        None, help="SQLite database path for tracking export state"
    ),
) -> None:
    spec = load_radar_config(config)
    report_path = radar_json or str(latest_radar_report_path(spec.output_dir))
    resolved_section = section or spec.export_section
    resolved_categories = (category or []) or spec.export_categories
    resolved_exclude_categories = (
        exclude_category or []
    ) or spec.export_exclude_categories
    resolved_top = top if top is not None else spec.export_top
    resolved_min_citations = (
        min_citations if min_citations is not None else spec.export_min_citations
    )
    resolved_max_citations = (
        max_citations if max_citations is not None else spec.export_max_citations
    )
    resolved_since = since or spec.export_since
    resolved_lookback_days = (
        lookback_days if lookback_days is not None else spec.export_lookback_days
    )
    target = export_radar_manifest(
        report_path,
        section=resolved_section,
        categories=resolved_categories,
        exclude_categories=resolved_exclude_categories,
        top=resolved_top,
        min_citations=resolved_min_citations,
        max_citations=resolved_max_citations,
        since=resolved_since,
        lookback_days=resolved_lookback_days,
        output_path=output,
        db_path=db,
    )
    typer.echo(f"wrote {target}")
    summary = summarize_manifest(load_manifest(str(target)))
    if summary["selected_count"] == 0:
        typer.secho(
            "No papers matched the current export filters.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.secho(
            _format_filter_summary(
                section=resolved_section,
                categories=resolved_categories,
                exclude_categories=resolved_exclude_categories,
                top=resolved_top,
                min_citations=resolved_min_citations,
                max_citations=resolved_max_citations,
                since=resolved_since,
                lookback_days=resolved_lookback_days,
            ),
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command("manifest-summary")
def manifest_summary_command(
    manifest: str = typer.Option(..., help="Manifest path to inspect"),
) -> None:
    data = load_manifest(manifest)
    summary = summarize_manifest(data)
    typer.echo(f"manifest={manifest}")
    typer.echo(f"selected_count={summary['selected_count']}")
    typer.echo(f"entry_count={summary['entry_count']}")
    typer.echo(f"storage_root={summary['storage_root']}")
    typer.echo(f"with_pdf_url={summary['with_pdf_url']}")
    typer.echo(f"with_target_path={summary['with_target_path']}")
    typer.echo("categories:")
    for name, count in sorted(summary["categories"].items()):
        typer.echo(f"  {name}: {count}")
    typer.echo("sections:")
    for name, count in sorted(summary["sections"].items()):
        typer.echo(f"  {name}: {count}")


@app.command("cache-summary")
def cache_summary_command(
    manifest: str = typer.Option(
        ..., help="Manifest path to inspect against the cache"
    ),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR, help="Local PDF cache directory"
    ),
) -> None:
    data = load_manifest(manifest)
    summary = summarize_cache(data, cache_dir)
    typer.echo(f"manifest={manifest}")
    typer.echo(f"cache_dir={summary['cache_dir']}")
    typer.echo(f"manifest_entries={summary['manifest_entries']}")
    typer.echo(f"cacheable_entries={summary['cacheable_entries']}")
    typer.echo(f"cached_entries={summary['cached_entries']}")
    typer.echo(f"missing_entries={summary['missing_entries']}")
    typer.echo(f"total_bytes={summary['total_bytes']}")


@app.command("cache-prime")
def cache_prime_command(
    manifest: str = typer.Option(..., help="Manifest path to prime into the cache"),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR, help="Local PDF cache directory"
    ),
) -> None:
    summary = asyncio.run(prime_cache(manifest, cache_dir))
    typer.echo(f"manifest={manifest}")
    typer.echo(f"cache_dir={summary['cache_dir']}")
    typer.echo(f"downloaded_entries={summary['downloaded_entries']}")
    typer.echo(f"reused_entries={summary['reused_entries']}")
    typer.echo(f"cached_entries={summary['cached_entries']}")
    typer.echo(f"missing_entries={summary['missing_entries']}")
    typer.echo(f"total_bytes={summary['total_bytes']}")


@app.command("stage")
def stage_command(
    manifest: str = typer.Option(..., help="Manifest path to stage locally"),
    cache_dir: str = typer.Option(
        DEFAULT_PDF_CACHE_DIR, help="Local PDF cache directory"
    ),
    output: str | None = typer.Option(
        None,
        help=f"Staged manifest output path; defaults to {DEFAULT_STAGED_MANIFEST_PATH}",
    ),
) -> None:
    target = asyncio.run(stage_manifest(manifest, cache_dir, output))
    typer.echo(f"wrote {target}")


@app.command("tui")
def radar_tui_command(
    config: str = typer.Option(
        DEFAULT_RADAR_CONFIG,
        help="Path to an arXiv radar YAML config",
    ),
    radar_json: str | None = typer.Option(
        None,
        help="Specific radar JSON report to curate; defaults to the latest report from the config output dir",
    ),
    output: str | None = typer.Option(
        None,
        help=f"Curated manifest output path; defaults to {DEFAULT_CURATED_MANIFEST_PATH}",
    ),
) -> None:
    raise typer.Exit(asyncio.run(run_radar_tui(config, radar_json, output)))


@app.command("db-init")
def db_init_command(
    db: str = typer.Option(DEFAULT_DB_PATH, help="SQLite database path"),
    seed: bool = typer.Option(
        True, help="Seed the database from existing JSON artifacts"
    ),
) -> None:
    created = init_db(db)
    typer.echo(f"database={'created' if created else 'already exists'}: {db}")
    if seed:
        with get_db(db) as conn:
            result = seed_from_json_artifacts(conn)
            typer.echo(f"seeded reports={len(result['reports'])}")
            for r in result["reports"]:
                typer.echo(f"  {r['path']}: {r['paper_count']} papers")
            if result["curated"]:
                typer.echo(f"curated: {result['curated']['paper_count']} papers")
            for m in result["manifests"]:
                typer.echo(f"manifest: {m['paper_count']} papers")
            if result["staged"]:
                typer.echo(f"staged: {result['staged']['paper_count']} papers")
            if result["extractions"]:
                typer.echo(f"extractions: {result['extractions']} from markdown-cache")


@app.command("db-status")
def db_status_command(
    db: str = typer.Option(DEFAULT_DB_PATH, help="SQLite database path"),
) -> None:
    init_db(db)
    with get_db(db) as conn:
        status = db_status(conn)
    typer.echo(f"papers={status['papers']}")
    typer.echo(f"radar_reports={status['reports']}")
    typer.echo(f"export_batches={status['export_batches']}")
    typer.echo(f"devices={status['devices']}")
    typer.echo(f"sync_sessions={status['sync_sessions']}")
    typer.echo(f"retrievals={status['retrievals']}")
    typer.echo(f"source_refreshes={status['source_refreshes']}")
    typer.echo(f"extractions: completed={status['extractions_completed']} pending={status['extractions_pending']}")
    if status["extraction_type_counts"]:
        typer.echo("  extraction breakdown:")
        for key, count in sorted(status["extraction_type_counts"].items()):
            typer.echo(f"    {key}: {count}")
    typer.echo(f"enrichments: completed={status['enrichments_completed']} pending={status['enrichments_pending']}")
    if status.get("enrichment_type_counts"):
        typer.echo("  enrichment breakdown:")
        for key, count in sorted(status["enrichment_type_counts"].items()):
            typer.echo(f"    {key}: {count}")
    typer.echo(f"reference_edges: {status.get('reference_edges', 0)} ({status.get('reference_targets', 0)} unique targets)")
    typer.echo(f"periodical_builds: {status.get('periodical_builds', 0)}")
    if status.get("periodical_last"):
        pl = status["periodical_last"]
        typer.echo(f"  last: {pl['started_at'][:19]} status={pl['status']} papers={pl['paper_count']} foundational={pl['foundational_count']}")
    typer.echo("curation:")
    for state, count in sorted(status["curation_counts"].items()):
        typer.echo(f"  {state}: {count}")
    if status["recent_papers"]:
        typer.echo("recent papers:")
        for p in status["recent_papers"]:
            ts = p.get("retrieved_at") or p["discovered_at"]
            src = p.get("retrieval_source") or ""
            typer.echo(f"  {p['arxiv_id']} ({ts[:10]} via {src}) {p['title'][:60]}")


@app.command("reconcile")
def reconcile_command(
    host: str = typer.Option(..., help="BOOX Drop host URL"),
    db: str = typer.Option(DEFAULT_DB_PATH, help="SQLite database path"),
    device_name: str = typer.Option("", help="Friendly device name"),
    env_file: str = typer.Option(".env", help="Optional env file path"),
    password: str | None = typer.Option(
        None, help="BOOX Drop password; the CLI derives the token locally"
    ),
) -> None:
    init_db(db)

    async def _reconcile() -> int:
        from booxdrop_cli import build_client, resolve_runtime_inputs, gather_state, DEFAULT_STORAGE_ROOT

        runtime = resolve_runtime_inputs(
            env_file, host, None, password, None,
            require_host=True, require_contract=False,
        )
        client = build_client(runtime)
        await client.init()

        with get_db(db) as conn:
            device = register_device(conn, host, device_name)
            typer.echo(f"device: {device.name} (id={device.id}, host={device.host})")

            from booxdrop_cli import PlanSpec
            spec = PlanSpec(
                storage_root=DEFAULT_STORAGE_ROOT,
                scan_dirs=["/storage/emulated/0/Books", "/storage/emulated/0/Download"],
                categories=[],
            )
            state = await gather_state(client, spec)

            device_files = state["discovered_files"]
            typer.echo(f"device files discovered: {len(device_files)}")

            result = reconcile_device(conn, device.id, device_files)
            typer.echo(f"reconciled={result['reconciled']} newly_tracked={result['newly_tracked']}")

            synced = get_synced_papers(conn, device.id)
            typer.echo(f"confirmed on device: {len(synced)}")

            pending = get_pending_sync(conn, device.id)
            typer.echo(f"pending sync: {len(pending)}")
            for p in pending[:10]:
                typer.echo(f"  {p['arxiv_id']} {p['title'][:60]}")
            if len(pending) > 10:
                typer.echo(f"  ... and {len(pending) - 10} more")

        return 0

    raise typer.Exit(asyncio.run(_reconcile()))


@app.command("extraction-status")
def extraction_status_command(
    db: str = typer.Option(DEFAULT_DB_PATH, help="SQLite database path"),
    extraction_type: str = typer.Option(
        "pdf_to_markdown", help="Extraction type to query"
    ),
) -> None:
    init_db(db)
    with get_db(db) as conn:
        pending = pending_extractions(conn, extraction_type)
    typer.echo(f"pending/failed {extraction_type} extractions: {len(pending)}")
    for p in pending:
        typer.echo(f"  {p['arxiv_id']} (status={p['status']}) {p['title'][:60]}")
        if p.get("error_msg"):
            typer.echo(f"    error: {p['error_msg'][:80]}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
