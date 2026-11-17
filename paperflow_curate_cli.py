"""Dedicated curator commands. All operations go through the FastAPI service."""
from __future__ import annotations

import json
import math
import click
import sys
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import typer

from paperflow_api_client import APIClient, APIError
from paperflow_settings import load_research_settings

app = typer.Typer(invoke_without_command=True, help="Discover → collect → assess → export. Run without arguments for a quick start.")
api_app = typer.Typer(no_args_is_help=True, help="Inspect and call the FastAPI service")


def configure(ctx: typer.Context,
              api_url: str | None = typer.Option(None, help="Service URL override"),
              config: str = typer.Option("arxiv-radar.yaml", help="Service settings"),
              json_output: bool = typer.Option(False, "--json", help="Complete JSON for scripts"),
              full: bool = typer.Option(False, "--full", help="Show all fields and source text"),
              measure: bool = typer.Option(False, "--measure", help="Report output context size on stderr"),
              request_id: str | None = typer.Option(None, "--request-id", help="Reuse this key when retrying the same write")):
    if request_id is not None and not 1 <= len(request_id) <= 200:
        raise typer.BadParameter("request-id must be 1–200 characters")
    ctx.meta.update(json_output=json_output, full=full, measure=measure,
                    raw=ctx.info_name == "api")
    if ctx.invoked_subcommand is None and ctx.info_name != "api":
        typer.echo("Curate a research question in four steps:\n"
                   "  1. items --query TOPIC              Discover sources (10 at a time)\n"
                   "     item ITEM_ID                    Inspect excerpts and revision IDs\n"
                   "  2. bucket-create TITLE --question QUESTION\n"
                   "     add BUCKET_ID ITEM_ID           Collect a source\n"
                   "  3. bucket BUCKET_ID                 Inspect claims and review gaps\n"
                   "     claim-show CLAIM_ID             Drill into evidence and reviews\n"
                   "  4. export BUCKET_ID --output brief.md\n\n"
                   "Prefix each command with paperflow curate.\n"
                   "Use --help for assessment/import commands; --json or --full before the command\n"
                   "for complete details. Add --measure to measure output context.")
        return
    if api_url is None:
        service = load_research_settings(config).service
        host = "127.0.0.1" if service.host == "0.0.0.0" else service.host
        host = f"[{host}]" if ":" in host else host
        api_url = f"http://{host}:{service.port}"
    ctx.obj = APIClient(api_url)
    ctx.obj.idempotency_key = request_id


app.callback()(configure)
api_app.callback()(configure)


def call(ctx, method, path, body=None):
    try:
        return ctx.obj.request(method, path, body)
    except (APIError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


def short(value, size=160):
    text = " ".join(str(value).split())
    return text if len(text) <= size else text[:size - 1] + "…"


def compact(value):
    """Bound text and collections without hiding review status or source basis."""
    def row(record):
        label = record.get("title", record.get("statement", record.get("quote", record.get("rationale", "Saved"))))
        return f"{record.get('id', '')}  {short(label)}"

    if isinstance(value, list):
        rows = [row(record) for record in value[:10]]
        rows.append(f"{len(value)} returned; {min(len(value), 10)} shown. Use --json for all returned records.")
        return "\n".join(rows)
    if "records" in value:
        return compact(value["records"]) + f"\nTotal: {value['total']}; next offset: {value['next_offset']}"
    if "item" in value and "revisions" in value:
        item = value["item"]
        lines = [row(item), f"Kind: {item['kind']} | Source: {item.get('canonical_url') or 'local'}",
                 f"{value.get('observation_count', len(value['observations']))} observations; {value.get('revision_page', {}).get('total', len(value['revisions']))} revisions"]
        for revision in value["revisions"][-3:]:
            lines += [f"Revision {revision['id']} | {revision['evidence_basis']}", short(revision.get("preview_text", revision.get("markdown", "")), 240)]
        lines.append("Next: add BUCKET_ID ITEM_ID; annotate REVISION_ID --quote PASSAGE --creator NAME")
        return "\n".join(lines)
    if "bucket" in value:
        lines = [row(value['bucket']), f"Status: {value['bucket'].get('status', 'active')} | Version: {value['bucket'].get('version', 1)}", short(value['bucket']['research_question']),
                 f"{value.get('member_page', {}).get('total', len(value['members']))} sources; {value.get('claim_page', {}).get('total', len(value['claims']))} claims"]
        lines += [row(m['item']) + f" [{m['membership']['role']}]\n  Membership {m['membership']['id']} version {m['membership'].get('version', 1)}" for m in value['members'][:10]]
        for claim in value['claims'][:10]:
            count = claim.get('evidence_count', len(claim.get('evidence', [])))
            lines.append(row(claim['claim']) + f" [{claim['review_status']}; {count} evidence; version {claim['claim'].get('version', 1)}]")
            if claim["claim"].get("superseded_by"):
                lines.append("  Superseded by " + claim["claim"]["superseded_by"])
        lines.append("Showing up to 10 sources and claims. Next: claim-show CLAIM_ID; export BUCKET_ID")
        return "\n".join(lines)
    if "claim" in value:
        lines = [row(value['claim']), f"Review: {value['review_status']}", "Scope: " + short(value['claim']['scope'])]
        lines.append(f"Version: {value['claim'].get('version', 1)} | Superseded by: {value['claim'].get('superseded_by') or 'none'}")
        for evidence in value['evidence'][:10]:
            assessment = evidence['assessment']
            lines += [f"{assessment['id']}  {assessment['stance']} [{evidence['review_status']}]",
                      short(assessment['rationale']),
                      f"Revision {evidence['revision']['id']} | {evidence['revision']['evidence_basis']}"]
        lines.append(f"{value.get('evidence_count', len(value['evidence']))} evidence; up to 10 shown. Next: review ASSESSMENT_ID DECISION --reviewer NAME")
        return "\n".join(lines)
    lines = [row(value)]
    for key in ("item_id", "revision_id", "claim_id", "bucket_id", "stance", "decision", "creator_kind", "reviewer_kind", "processor_version", "processing_run_id", "evidence_basis", "version", "active", "status", "superseded_by"):
        if value.get(key) is not None:
            lines.append(f"{key}: {value[key]}")
    return "\n".join(lines)


def emit(value, hint=""):
    ctx = click.get_current_context()
    raw = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    expanded = ctx.meta.get("json_output") or ctx.meta.get("full") or ctx.meta.get("raw")
    rendered = raw if expanded or isinstance(value, str) else compact(value)
    if hint and not expanded:
        rendered += "\n" + hint
    typer.echo(rendered)
    if ctx.meta.get("measure"):
        # Counts describe serialized response/output, not network bytes or model tokenization.
        report = {"output_chars": len(rendered), "output_bytes": len(rendered.encode("utf-8")),
                  "estimated_tokens": math.ceil(len(rendered) / 4),
                  "estimate_method": "ceil(characters/4); not tokenizer measured",
                  "expanded_chars": len(raw),
                  "reduction_percent": round(100 * (1 - len(rendered) / max(1, len(raw))), 1)}
        report["backend"] = getattr(ctx.obj, "last_measurement", {})
        report["projection"] = value.get("projection", "full") if isinstance(value, dict) else "full"
        typer.echo(json.dumps(report), err=True)


@api_app.command("request")
def request(ctx: typer.Context, method: str, path: str,
            body: Path | None = typer.Option(None, help="JSON file; use - to read stdin")):
    payload = None
    if body is not None:
        try:
            payload = json.loads(sys.stdin.read() if str(body) == "-" else body.read_text())
        except (OSError, ValueError) as exc:
            raise typer.BadParameter("body must be a readable JSON file or JSON on stdin") from exc
    emit(call(ctx, method, path, payload))


@api_app.command("routes")
def routes(ctx: typer.Context):
    schema = call(ctx, "GET", "/openapi.json")
    emit([{ "method": method.upper(), "path": path, "summary": operation.get("summary", "")}
          for path, operations in schema["paths"].items() for method, operation in operations.items()
          if method in {"get", "post", "patch", "put", "delete"}])


@app.command("items", rich_help_panel="Discover and collect")
def items(ctx: typer.Context, channel: str | None = None, query: str | None = None,
          limit: int = typer.Option(10, min=1, max=200), offset: int = typer.Option(0, min=0)):
    filters = {k: v for k, v in dict(channel=channel, query=query, limit=limit, offset=offset).items() if v is not None}
    emit(call(ctx, "GET", "/api/research/items?" + urlencode(filters)),
         f"Next: item ITEM_ID | More: items --offset {offset + limit} --limit {limit} (retain your filters)")


@app.command("item", rich_help_panel="Discover and collect")
def item(ctx: typer.Context, item_id: UUID):
    emit(call(ctx, "GET", f"/api/research/items/{item_id}" + ("" if ctx.meta.get("full") or ctx.meta.get("json_output") else "?view=compact")))


@app.command("item-add", rich_help_panel="Import source material")
def item_add(ctx: typer.Context, title: str, kind: str = "article", url: str | None = None):
    emit(call(ctx, "POST", "/api/research/items", {"title": title, "kind": kind, "canonical_url": url}))


@app.command("revision-add", rich_help_panel="Import source material")
def revision_add(ctx: typer.Context, item_id: UUID, file: Path = typer.Option(..., exists=True, dir_okay=False),
                 basis: str = "excerpt", processor_version: str = "manual-v1"):
    emit(call(ctx, "POST", f"/api/research/items/{item_id}/revisions",
              {"markdown": file.read_text(), "evidence_basis": basis, "processor_version": processor_version}))


@app.command("buckets", rich_help_panel="Discover and collect")
def buckets(ctx: typer.Context, limit: int = typer.Option(10, min=1, max=200), offset: int = typer.Option(0, min=0)):
    emit(call(ctx, "GET", f"/api/research/buckets?limit={limit}&offset={offset}"))


@app.command("bucket-create", rich_help_panel="Discover and collect")
def bucket_create(ctx: typer.Context, title: str, question: str = typer.Option(...), description: str = "",
                  manuscript: UUID | None = None):
    emit(call(ctx, "POST", "/api/research/buckets", {"title": title, "research_question": question,
              "description": description, "target_manuscript_id": str(manuscript) if manuscript else None}))


@app.command("bucket", rich_help_panel="Discover and collect")
def bucket(ctx: typer.Context, bucket_id: UUID):
    emit(call(ctx, "GET", f"/api/research/buckets/{bucket_id}" + ("" if ctx.meta.get("full") or ctx.meta.get("json_output") else "?view=compact")))


@app.command("add", rich_help_panel="Discover and collect")
def add(ctx: typer.Context, bucket_id: UUID, item_id: UUID, role: str = "collected", note: str = ""):
    emit(call(ctx, "POST", f"/api/research/buckets/{bucket_id}/members", {"item_id": str(item_id), "role": role, "note": note}))


@app.command("annotate", rich_help_panel="Assess evidence")
def annotate(ctx: typer.Context, revision_id: UUID, quote: str = typer.Option(...),
             creator: str = typer.Option(...), start: int | None = None, note: str = "",
             creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    emit(call(ctx, "POST", f"/api/research/revisions/{revision_id}/annotations",
              {"quote": quote, "creator": creator, "start": start, "note": note, "creator_kind": creator_kind, "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("claim", rich_help_panel="Assess evidence")
def claim(ctx: typer.Context, bucket_id: UUID, statement: str,
          creator: str = typer.Option(...), scope: str = "", annotation: UUID | None = None,
          creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    emit(call(ctx, "POST", f"/api/research/buckets/{bucket_id}/claims",
              {"statement": statement, "scope": scope, "creator": creator, "creator_kind": creator_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None,
               "source_annotation_id": str(annotation) if annotation else None}))


@app.command("evidence", rich_help_panel="Assess evidence")
def evidence(ctx: typer.Context, claim_id: UUID, revision_id: UUID,
             stance: str = typer.Option(...), rationale: str = typer.Option(...), creator: str = typer.Option(...),
             annotation: UUID | None = None, creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    emit(call(ctx, "POST", f"/api/research/claims/{claim_id}/evidence",
              {"revision_id": str(revision_id), "stance": stance, "rationale": rationale, "creator": creator,
               "annotation_id": str(annotation) if annotation else None, "creator_kind": creator_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("review", rich_help_panel="Assess evidence")
def review(ctx: typer.Context, assessment_id: UUID, decision: str,
           reviewer: str = typer.Option(...), note: str = "", reviewer_kind: str = "human",
           processor_version: str | None = None, processing_run_id: UUID | None = None):
    emit(call(ctx, "POST", f"/api/research/evidence/{assessment_id}/reviews",
              {"decision": decision, "reviewer": reviewer, "note": note, "reviewer_kind": reviewer_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("review-claim", rich_help_panel="Assess evidence")
def review_claim(ctx: typer.Context, claim_id: UUID, decision: str,
                 reviewer: str = typer.Option(...), note: str = "", reviewer_kind: str = "human",
           processor_version: str | None = None, processing_run_id: UUID | None = None):
    emit(call(ctx, "POST", f"/api/research/claims/{claim_id}/reviews",
              {"decision": decision, "reviewer": reviewer, "note": note, "reviewer_kind": reviewer_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("export", rich_help_panel="Discover and collect")
def export(ctx: typer.Context, bucket_id: UUID, output: Path | None = None):
    text = call(ctx, "GET", f"/api/research/buckets/{bucket_id}/export")
    if output is None:
        emit(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        typer.echo(str(output))


@app.command("claim-show", rich_help_panel="Assess evidence")
def claim_show(ctx: typer.Context, claim_id: UUID):
    """Inspect a claim's evidence and current review decisions."""
    emit(call(ctx, "GET", f"/api/research/claims/{claim_id}" + ("" if ctx.meta.get("full") or ctx.meta.get("json_output") else "?view=compact")))


@app.command("resolve", rich_help_panel="Import source material")
def resolve(ctx: typer.Context, namespace: str, value: str):
    """Find an existing item by an authoritative identifier."""
    emit(call(ctx, "GET", "/api/research/identifiers/resolve?" + urlencode(dict(namespace=namespace, value=value))))


@app.command("identify", rich_help_panel="Import source material")
def identify(ctx: typer.Context, item_id: UUID, namespace: str, value: str):
    emit(call(ctx, "POST", f"/api/research/items/{item_id}/identifiers", {"namespace": namespace, "value": value}))


@app.command("relate", rich_help_panel="Import source material")
def relate(ctx: typer.Context, source: UUID, target: UUID, kind: str, creator: str = typer.Option(...),
           note: str = "", revision: UUID | None = None, creator_kind: str = "human",
           processor_version: str | None = None, processing_run_id: UUID | None = None):
    """Record links_to, discusses, cites, or version_of; evidence uses assessments."""
    emit(call(ctx, "POST", f"/api/research/items/{source}/relationships",
              {"target_item_id": str(target), "kind": kind, "creator": creator, "note": note,
               "source_revision_id": str(revision) if revision else None, "creator_kind": creator_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("bucket-status", rich_help_panel="Maintain research")
def bucket_status(ctx: typer.Context, bucket_id: UUID, status: str,
                  version: int = typer.Option(..., min=1), creator: str = typer.Option(...),
                  creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    """Archive or reactivate a bucket using its current version."""
    emit(call(ctx, "PATCH", f"/api/research/buckets/{bucket_id}",
              {"status": status, "expected_version": version, "creator": creator, "creator_kind": creator_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))


@app.command("member-update", rich_help_panel="Maintain research")
def member_update(ctx: typer.Context, membership_id: UUID, version: int = typer.Option(..., min=1),
                  creator: str = typer.Option(...), note: str | None = None, role: str | None = None,
                  position: int | None = typer.Option(None, min=0), active: bool | None = typer.Option(None, "--active/--removed"),
                  creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    """Correct collection context, remove a source, or restore it; history is kept."""
    body = {"expected_version": version, "creator": creator, "creator_kind": creator_kind,
            "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}
    body.update({k:v for k,v in dict(note=note, role=role, position=position, active=active).items() if v is not None})
    emit(call(ctx, "PATCH", f"/api/research/memberships/{membership_id}", body))


@app.command("supersede", rich_help_panel="Maintain research")
def supersede(ctx: typer.Context, claim_id: UUID, replacement: UUID,
              version: int = typer.Option(..., min=1), creator: str = typer.Option(...),
                  creator_kind: str = "human", processor_version: str | None = None, processing_run_id: UUID | None = None):
    """Link a corrected claim while preserving the old claim and its evidence."""
    emit(call(ctx, "POST", f"/api/research/claims/{claim_id}/supersede",
              {"replacement_claim_id": str(replacement), "expected_version": version, "creator": creator, "creator_kind": creator_kind,
               "processor_version": processor_version, "processing_run_id": str(processing_run_id) if processing_run_id else None}))
