from __future__ import annotations

from rich.console import Console

import paperflow_terminal


def _capture_console(monkeypatch):
    console = Console(record=True, width=100)
    monkeypatch.setattr(paperflow_terminal, "console", console)
    return console


def test_print_pipeline_steps_uses_status_styles(monkeypatch):
    console = _capture_console(monkeypatch)

    paperflow_terminal.print_pipeline_steps(
        [
            ("read", "done", "loaded inputs"),
            ("write", "pending", "waiting"),
            ("fail", "error", "bad input"),
        ]
    )

    text = console.export_text()
    assert "read" in text
    assert "done" in text
    assert "pending" in text
    assert "error" in text


def test_print_threads_includes_focus_and_support(monkeypatch):
    console = _capture_console(monkeypatch)

    paperflow_terminal.print_threads(
        [
            {
                "id": "thread-retrieval",
                "title": "Retrieval as an evergreen research thread",
                "focus": "arxiv:2401.00001",
                "focus_title": "Retrieval Evaluation",
                "source_count": 2,
                "paper_count": 2,
                "supporting_papers": [
                    {
                        "paper_key": "arxiv:2401.00002",
                        "title": "Retrieval Grounding",
                    }
                ],
            }
        ]
    )

    text = console.export_text()
    assert "thread-retrieval" in text
    assert "Retrieval Evaluation" in text
    assert "Retrieval Grounding" in text
