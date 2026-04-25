"""Typer app entry point and output helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import BaseModel

if TYPE_CHECKING:
    from spacedrep.core import SpacedrepError

app = typer.Typer(
    name="spacedrep",
    help="Agent-first flashcard CLI with FSRS scheduling and .apkg support",
    no_args_is_help=True,
)

DB_DEFAULT = typer.Option(
    Path("./collection.anki21"),
    "--db",
    envvar="SPACEDREP_DB",
    help="Path to SQLite database (env: SPACEDREP_DB)",
)


def output_json(data: BaseModel | dict[str, Any] | list[Any]) -> None:
    """Serialize to JSON and write to stdout."""
    if isinstance(data, BaseModel):
        sys.stdout.write(data.model_dump_json())
    else:
        sys.stdout.write(json.dumps(data, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def output_quiet(value: str | int | list[str] | list[int]) -> None:
    """Write bare values to stdout, one per line. For pipe-friendly output."""
    if isinstance(value, list):
        for v in value:
            sys.stdout.write(f"{v}\n")
    else:
        sys.stdout.write(f"{value}\n")
    sys.stdout.flush()


def output_error(err: SpacedrepError) -> None:
    """Write error JSON to stderr."""
    error_data: dict[str, object] = {
        "error": err.error_code,
        "message": err.message,
        "suggestion": err.suggestion,
        **err.extra,
    }
    sys.stderr.write(json.dumps(error_data, default=str))
    sys.stderr.write("\n")
    sys.stderr.flush()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(None, "--version", "-v", help="Show version"),
) -> None:
    """Agent-first flashcard CLI with FSRS scheduling and .apkg support."""
    if version:
        from importlib.metadata import version as get_version

        typer.echo(f"spacedrep {get_version('spacedrep')}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# Register subcommands
from spacedrep.commands.card import card_app  # noqa: E402
from spacedrep.commands.db_cmd import db_app  # noqa: E402
from spacedrep.commands.deck import deck_app  # noqa: E402
from spacedrep.commands.fsrs_cmd import fsrs_app  # noqa: E402
from spacedrep.commands.review import review_app  # noqa: E402
from spacedrep.commands.stats import stats_app  # noqa: E402

app.add_typer(db_app)
app.add_typer(card_app)
app.add_typer(review_app)
app.add_typer(deck_app)
app.add_typer(stats_app)
app.add_typer(fsrs_app)
