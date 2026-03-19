"""Deck management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

deck_app = typer.Typer(name="deck", help="Deck operations")


@deck_app.command("list")
def list_decks(
    db: Path = DB_DEFAULT,
) -> None:
    """List all decks with card and due counts.

    Example:
        spacedrep deck list
    """
    try:
        result = core.list_decks(db)
        output_json([d.model_dump() for d in result])
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@deck_app.command("import")
def import_deck(
    path: Path = typer.Argument(..., help="Path to .apkg file"),
    question_field: str | None = typer.Option(None, "--question-field", help="Question field name"),
    answer_field: str | None = typer.Option(None, "--answer-field", help="Answer field name"),
    db: Path = DB_DEFAULT,
) -> None:
    """Import an .apkg deck file.

    Example:
        spacedrep deck import ~/Downloads/DSA_FAANG.apkg
        spacedrep deck import deck.apkg --question-field Prompt --answer-field Implementation
    """
    try:
        result = core.import_deck(db, path, question_field, answer_field)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@deck_app.command("export")
def export_deck(
    path: Path = typer.Argument(..., help="Output .apkg file path"),
    deck: str | None = typer.Option(None, "--deck", help="Export only this deck"),
    db: Path = DB_DEFAULT,
) -> None:
    """Export cards to an .apkg file.

    Example:
        spacedrep deck export ./export.apkg --deck AWS
    """
    try:
        count = core.export_deck(db, path, deck)
        output_json({"exported": count, "file": str(path)})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
