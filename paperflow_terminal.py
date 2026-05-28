from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


console = Console()


def print_header(title: str, subtitle: str | None = None) -> None:
    body = title if subtitle is None else f"{title}\n[dim]{subtitle}[/dim]"
    console.print(Panel.fit(body, border_style="blue"))


def print_kv(items: dict[str, Any]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="dim")
    table.add_column("Value")
    for key, value in items.items():
        table.add_row(str(key), str(value))
    console.print(table)


def print_pipeline_steps(steps: Iterable[tuple[str, str, str]]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Step", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail")
    for step, status, detail in steps:
        style = "green" if status == "done" else "yellow" if status == "pending" else "red"
        table.add_row(step, f"[{style}]{status}[/{style}]", detail)
    console.print(table)


def print_threads(topics: list[dict[str, Any]]) -> None:
    for index, topic in enumerate(topics, start=1):
        table = Table(title=f"{index}. {topic['title']}", show_header=False)
        table.add_column("Field", style="dim", no_wrap=True)
        table.add_column("Value")
        table.add_row("id", str(topic["id"]))
        table.add_row("focus", str(topic["focus"]))
        table.add_row("focus title", str(topic["focus_title"]))
        table.add_row("sources", str(topic["source_count"]))
        table.add_row("papers", str(topic["paper_count"]))
        support = topic.get("supporting_papers") or []
        if support:
            table.add_row(
                "support",
                "\n".join(
                    f"{paper['paper_key']}  {paper['title']}" for paper in support
                ),
            )
        console.print(table)


def print_queue(topics: list[dict[str, Any]]) -> None:
    table = Table(title="Periodical Queue")
    table.add_column("ID", style="cyan")
    table.add_column("Issue", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Focus")
    table.add_column("Title")
    for topic in topics:
        table.add_row(
            str(topic.get("id") or ""),
            str(topic.get("issue") or ""),
            str(topic.get("status") or ""),
            str(topic.get("focus") or ""),
            str(topic.get("title") or ""),
        )
    console.print(table)
