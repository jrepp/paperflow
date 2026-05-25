from __future__ import annotations

import asyncio
import urllib.error

import typer
import websockets

from booxdrop_cli import (
    build_client,
    resolve_runtime_inputs,
    run_inventory,
    run_organize,
    run_sync_staged_manifest,
    run_validate,
)

DEFAULT_DB_PATH = "artifacts/radar.db"


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="BOOX device operations",
)


def _looks_like_disconnect(exc: Exception) -> bool:
    if isinstance(
        exc,
        (TimeoutError, urllib.error.URLError, OSError, websockets.WebSocketException),
    ):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "timed out",
            "opening handshake",
            "connect call failed",
            "connection refused",
            "network is unreachable",
            "no route to host",
        )
    )


def _run_boox_async(coro: object) -> int:
    try:
        return asyncio.run(coro)
    except Exception as exc:
        if not _looks_like_disconnect(exc):
            raise
        typer.secho(
            "BOOX host is unavailable. The tablet may be asleep, off Wi-Fi, or still waking up.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Wake the tablet, keep BOOX Drop open/listening, then rerun the same command. Retrying is safe.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.secho(f"Last error: {exc}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(2)


@app.command("inventory")
def inventory_command(
    env_file: str = typer.Option(".env", help="Optional env file path"),
    host: str | None = typer.Option(None, help="BOOX Drop host URL"),
    token: str | None = typer.Option(
        None, help='Basic auth token, usually base64(":<password>")'
    ),
    password: str | None = typer.Option(
        None, help="BOOX Drop password; the CLI derives the token locally"
    ),
    contract: str | None = typer.Option(
        None, help="Optional sync contract path for scoped inventory context"
    ),
) -> None:
    async def _command() -> int:
        runtime = resolve_runtime_inputs(
            env_file,
            host,
            token,
            password,
            contract,
            require_host=True,
            require_contract=False,
        )
        client = build_client(runtime)
        await client.init()
        return await run_inventory(client, runtime.spec)

    raise typer.Exit(_run_boox_async(_command()))


@app.command("sync")
def sync_command(
    apply: bool = typer.Option(False, help="Apply changes instead of dry-run planning"),
    settle_seconds: int = typer.Option(
        5, help="Wait time after physical moves before shelving"
    ),
    env_file: str = typer.Option(".env", help="Optional env file path"),
    host: str | None = typer.Option(None, help="BOOX Drop host URL"),
    token: str | None = typer.Option(
        None, help='Basic auth token, usually base64(":<password>")'
    ),
    password: str | None = typer.Option(
        None, help="BOOX Drop password; the CLI derives the token locally"
    ),
    contract: str | None = typer.Option(None, help="Path to a sync contract file"),
) -> None:
    async def _command() -> int:
        runtime = resolve_runtime_inputs(
            env_file,
            host,
            token,
            password,
            contract,
            require_host=True,
            require_contract=True,
        )
        assert runtime.spec is not None
        client = build_client(runtime)
        await client.init()
        return await run_organize(client, runtime.spec, apply, settle_seconds)

    raise typer.Exit(_run_boox_async(_command()))


@app.command("sync-manifest")
def sync_manifest_command(
    manifest: str = typer.Option(..., help="Staged manifest path with local PDFs"),
    apply: bool = typer.Option(
        False, help="Upload staged papers instead of dry-run planning"
    ),
    settle_seconds: int = typer.Option(
        5, help="Wait time for BOOX indexing before shelf sync"
    ),
    env_file: str = typer.Option(".env", help="Optional env file path"),
    host: str | None = typer.Option(None, help="BOOX Drop host URL"),
    token: str | None = typer.Option(
        None, help='Basic auth token, usually base64(":<password>")'
    ),
    password: str | None = typer.Option(
        None, help="BOOX Drop password; the CLI derives the token locally"
    ),
    db: str | None = typer.Option(
        None, help="SQLite database path for tracking sync state"
    ),
) -> None:
    async def _command() -> int:
        runtime = resolve_runtime_inputs(
            env_file,
            host,
            token,
            password,
            None,
            require_host=True,
            require_contract=False,
        )
        client = build_client(runtime)
        await client.init()
        return await run_sync_staged_manifest(
            client, manifest, apply, settle_seconds,
            db_path=db,
        )

    raise typer.Exit(_run_boox_async(_command()))


@app.command("validate")
def validate_command(
    env_file: str = typer.Option(".env", help="Optional env file path"),
    host: str | None = typer.Option(None, help="BOOX Drop host URL"),
    token: str | None = typer.Option(
        None, help='Basic auth token, usually base64(":<password>")'
    ),
    password: str | None = typer.Option(
        None, help="BOOX Drop password; the CLI derives the token locally"
    ),
    contract: str | None = typer.Option(None, help="Path to a sync contract file"),
) -> None:
    async def _command() -> int:
        runtime = resolve_runtime_inputs(
            env_file,
            host,
            token,
            password,
            contract,
            require_host=True,
            require_contract=True,
        )
        assert runtime.spec is not None
        client = build_client(runtime)
        await client.init()
        return await run_validate(client, runtime.spec)

    raise typer.Exit(_run_boox_async(_command()))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
