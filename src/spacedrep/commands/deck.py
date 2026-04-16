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
            output_json({"decks": [d.model_dump() for d in result], "count": len(result)})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@deck_app.command("import")
def import_deck(
    path: Path = typer.Argument(..., help="Path to .apkg file"),
    force: bool = typer.Option(
        False, "--force", help="Replace existing database without safety check"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Output card count only"),
    db: Path = DB_DEFAULT,
) -> None:
    """Open an .apkg deck file as the working database.

    Example:
        spacedrep deck import ~/Downloads/DSA_FAANG.apkg
        spacedrep deck import deck.apkg --force
        spacedrep deck import deck.apkg -q
    """
    try:
        result = core.open_deck(db, path, force=force)
        if quiet:
            output_quiet(result.card_count)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@deck_app.command("export")
def export_deck(
    path: Path = typer.Argument(..., help="Output .apkg file path"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Output exported count only"),
    db: Path = DB_DEFAULT,
) -> None:
    """Save the entire collection as an Anki .apkg file.

    Example:
        spacedrep deck export ./export.apkg
        spacedrep deck export ./export.apkg -q
    """
    try:
        result = core.save_deck(db, path)
        if quiet:
            output_quiet(result.card_count)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
