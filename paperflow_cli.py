from __future__ import annotations

import typer

import arxiv_radar_cli as radar_cli
import boox_sync_cli
from paperflow_periodical import (
    DEFAULT_PERIODICAL_QUEUE_PATH,
    add_periodical_queue_item,
    load_periodical_queue,
    propose_periodical_topics,
)
from paperflow_publish import (
    DEFAULT_PUBLISHING_CORPUS_PATH,
    build_publishing_corpus,
    load_publishing_corpus,
    propose_publishing_threads,
)
from paperflow_terminal import (
    print_header,
    print_kv,
    print_pipeline_steps,
    print_queue,
    print_threads,
)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Project-centric research paper discovery, publishing, and delivery workflow",
)

radar_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Generate, refresh, curate, and export radar reports",
)
sources_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Ingest source-specific paper feeds into the shared radar shape",
)
library_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Prepare manifests, cache papers, stage local artifacts, and build summaries",
)
publish_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Build publishing corpora, plan durable issue threads, and produce periodicals",
)
queue_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the editorial queue for numbered periodical issues",
)
issue_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Build numbered periodical issues from approved topics",
)
project_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inspect and maintain project state",
)


@app.command("status")
def status_command(
    db: str = typer.Option(radar_cli.DEFAULT_DB_PATH, help="SQLite database path"),
) -> None:
    radar_cli.db_status_command(db=db)


@app.command("init")
def init_command(
    db: str = typer.Option(radar_cli.DEFAULT_DB_PATH, help="SQLite database path"),
    seed: bool = typer.Option(
        True, help="Seed the database from existing JSON artifacts"
    ),
) -> None:
    radar_cli.db_init_command(db=db, seed=seed)


radar_app.command("generate")(radar_cli.generate_command)
radar_app.command("refresh")(radar_cli.refresh_command)
radar_app.command("tui")(radar_cli.radar_tui_command)
radar_app.command("export")(radar_cli.export_command)

sources_app.command("arxiv-ingest")(radar_cli.arxiv_ingest_command)
sources_app.command("hf-papers")(radar_cli.huggingface_papers_command)

library_app.command("prepare")(radar_cli.prepare_command)
library_app.command("report")(radar_cli.report_command)
library_app.command("manifest-summary")(radar_cli.manifest_summary_command)
library_app.command("cache-summary")(radar_cli.cache_summary_command)
library_app.command("cache-prime")(radar_cli.cache_prime_command)
library_app.command("stage")(radar_cli.stage_command)


@publish_app.command("corpus")
def publish_corpus_command(
    artifacts: list[str] = typer.Argument(
        ..., help="Radar report or manifest JSON artifacts to ingest"
    ),
    output: str = typer.Option(
        DEFAULT_PUBLISHING_CORPUS_PATH,
        help="Output path for the merged publishing corpus",
    ),
) -> None:
    print_header("Publishing Corpus", "Merging radar artifacts into a deduplicated corpus")
    print_pipeline_steps(
        [
            ("read artifacts", "done", f"{len(artifacts)} input file(s)"),
            ("deduplicate papers", "pending", "source-neutral paper identity"),
            ("write corpus", "pending", output),
        ]
    )
    corpus = build_publishing_corpus(artifacts, output_path=output)
    print_pipeline_steps(
        [
            ("read artifacts", "done", f"{len(corpus['artifact_paths'])} input file(s)"),
            ("deduplicate papers", "done", f"{corpus['paper_count']} unique paper(s)"),
            ("write corpus", "done", output),
        ]
    )
    print_kv(
        {
            "output": output,
            "paper_count": corpus["paper_count"],
            "artifact_count": len(corpus["artifact_paths"]),
        }
    )


@publish_app.command("threads")
def publish_threads_command(
    corpus: str = typer.Option(
        DEFAULT_PUBLISHING_CORPUS_PATH,
        help="Merged publishing corpus path",
    ),
    limit: int = typer.Option(10, help="Number of thread candidates to present"),
    papers_per_thread: int = typer.Option(
        8, help="Maximum papers to attach to each proposed thread"
    ),
) -> None:
    print_header("Publishing Threads", "Candidate durable issue threads")
    payload = load_publishing_corpus(corpus)
    topics = propose_publishing_threads(
        payload,
        limit=limit,
        papers_per_thread=papers_per_thread,
    )
    print_kv({"corpus": corpus, "candidate_count": len(topics)})
    print_threads(topics)


@publish_app.command("topics")
def periodical_topics_command(
    manifest: str = typer.Option(
        radar_cli.DEFAULT_MANIFEST_PATH,
        help=f"Manifest path; defaults to {radar_cli.DEFAULT_MANIFEST_PATH}",
    ),
    limit: int = typer.Option(8, help="Number of candidate topics to present"),
    supporting_papers: int = typer.Option(
        6, help="Supporting papers to suggest per topic"
    ),
) -> None:
    print_header("Periodical Topics", "Candidate focal-paper issues from a manifest")
    topics = propose_periodical_topics(
        manifest,
        limit=limit,
        supporting_papers=supporting_papers,
    )
    for topic in topics:
        print_kv(
            {
                "id": topic["id"],
                "title": topic["title"],
                "focus": topic["focus"],
                "supporting_papers": len(topic["supporting_papers"]),
            }
        )


@queue_app.command("add")
def periodical_queue_add_command(
    focus: str = typer.Option(
        ...,
        help="Focal paper arXiv ID, resolved ID, or title substring",
    ),
    manifest: str = typer.Option(
        radar_cli.DEFAULT_MANIFEST_PATH,
        help=f"Manifest path; defaults to {radar_cli.DEFAULT_MANIFEST_PATH}",
    ),
    queue_path: str = typer.Option(
        DEFAULT_PERIODICAL_QUEUE_PATH,
        help="Periodical topic queue path",
    ),
    title: str | None = typer.Option(None, help="Editorial topic title"),
    series: str = typer.Option("Research Radar", help="Periodical series name"),
    issue: int | None = typer.Option(None, help="Numbered issue"),
    supporting_papers: int = typer.Option(6, help="Supporting papers to include"),
) -> None:
    item = add_periodical_queue_item(
        manifest_path=manifest,
        queue_path=queue_path,
        focus=focus,
        title=title,
        series=series,
        issue=issue,
        supporting_papers=supporting_papers,
    )
    print_header("Queued Periodical Issue")
    print_kv(
        {
            "queued": item["id"],
            "title": item["title"],
            "focus": item["focus"],
            "issue": item.get("issue") or "",
            "queue": queue_path,
        }
    )


@queue_app.command("list")
def periodical_queue_list_command(
    queue_path: str = typer.Option(
        DEFAULT_PERIODICAL_QUEUE_PATH,
        help="Periodical topic queue path",
    ),
) -> None:
    queue = load_periodical_queue(queue_path)
    topics = queue.get("topics", [])
    if not topics:
        print_header("Periodical Queue", "queue is empty")
        return
    print_queue(topics)

issue_app.command("build")(radar_cli.periodical_command)
publish_app.add_typer(queue_app, name="queue")
publish_app.add_typer(issue_app, name="issue")

project_app.command("db-init")(radar_cli.db_init_command)
project_app.command("db-status")(radar_cli.db_status_command)
project_app.command("reconcile")(radar_cli.reconcile_command)
project_app.command("extraction-status")(radar_cli.extraction_status_command)

app.add_typer(radar_app, name="radar")
app.add_typer(sources_app, name="sources")
app.add_typer(library_app, name="library")
app.add_typer(publish_app, name="publish")
app.add_typer(project_app, name="project")
app.add_typer(boox_sync_cli.app, name="device")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
