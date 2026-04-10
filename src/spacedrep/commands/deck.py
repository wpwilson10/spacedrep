"""Deck management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json, output_quiet

deck_app = typer.Typer(name="deck", help="Deck operations")


@deck_app.command("list")
def list_decks(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Output one deck name per line"),
    db: Path = DB_DEFAULT,
) -> None:
    """List all decks with card and due counts.

    Example:
        spacedrep deck list
        spacedrep deck list -q
    """
    try:
        result = core.list_decks(db)
        if quiet:
            output_quiet([d.name for d in result])
        else:
            output_json([d.model_dump() for d in result])
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@deck_app.command("import")
def import_deck(
    path: Path = typer.Argument(..., help="Path to .apkg file"),
    question_field: str | None = typer.Option(None, "--question-field", help="Question field name"),
    answer_field: str | None = typer.Option(None, "--answer-field", help="Answer field name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would be imported without writing"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Output imported count only"),
    db: Path = DB_DEFAULT,
) -> None:
    """Import an .apkg deck file.

    Example:
        spacedrep deck import ~/Downloads/DSA_FAANG.apkg
        spacedrep deck import deck.apkg --question-field Prompt --answer-field Implementation
        spacedrep deck import deck.apkg --dry-run
        spacedrep deck import deck.apkg -q
    """
    try:
        result = core.import_deck(db, path, question_field, answer_field, dry_run=dry_run)
        if quiet:
            output_quiet(result.imported)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


def _parse_tags(tags: str | None) -> list[str] | None:
    """Parse space-separated tags string into a list, or None if empty."""
    if not tags:
        return None
    return tags.split()


@deck_app.command("export")
def export_deck(
    path: Path = typer.Argument(..., help="Output .apkg file path"),
    deck: str | None = typer.Option(None, "--deck", help="Export only this deck"),
    tags: str | None = typer.Option(None, "--tags", help="Filter by space-separated tags"),
    state: str | None = typer.Option(None, "--state", help="Filter by state"),
    search: str | None = typer.Option(None, "--search", "-s", help="Search text filter"),
    suspended: bool | None = typer.Option(None, "--suspended", help="Filter by suspended status"),
    source: str | None = typer.Option(None, "--source", help="Filter by source"),
    buried: bool | None = typer.Option(None, "--buried", help="Filter by buried status"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Output exported count only"),
    db: Path = DB_DEFAULT,
) -> None:
    """Export cards to an .apkg file with optional filters.

    Example:
        spacedrep deck export ./export.apkg --deck AWS
        spacedrep deck export ./export.apkg --tags aws --state review
        spacedrep deck export ./export.apkg -q
    """
    try:
        count = core.export_deck(
            db,
            path,
            deck=deck,
            tags=_parse_tags(tags),
            state=state,
            search=search,
            suspended=suspended,
            source=source,
            buried=buried,
        )
        if quiet:
            output_quiet(count)
        else:
            output_json({"exported": count, "file": str(path)})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
