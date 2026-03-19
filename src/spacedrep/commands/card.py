"""Card management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

card_app = typer.Typer(name="card", help="Card operations")


@card_app.command("next")
def next_card(
    db: Path = DB_DEFAULT,
) -> None:
    """Get the next due card for review.

    Example:
        spacedrep card next
        spacedrep card next --db ~/study/reviews.db
    """
    try:
        result = core.get_next_card(db)
        if result is None:
            next_due = core.get_next_due_time(db)
            output_json({"card_id": None, "message": "No cards due", "next_due": next_due})
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("add")
def add_card(
    question: str = typer.Argument(..., help="The card question"),
    answer: str = typer.Argument(..., help="The card answer"),
    deck: str = typer.Option("Default", help="Deck name"),
    tags: str = typer.Option("", help="Comma-separated tags"),
    db: Path = DB_DEFAULT,
) -> None:
    """Add a new flashcard.

    Example:
        spacedrep card add "Why eventual consistency?" "Availability tradeoff" --deck AWS --tags s3
    """
    try:
        result = core.add_card(db, question, answer, deck=deck, tags=tags)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("suspend")
def suspend(
    card_id: int = typer.Argument(..., help="Card ID to suspend"),
    db: Path = DB_DEFAULT,
) -> None:
    """Suspend a card (exclude from reviews).

    Example:
        spacedrep card suspend 42
    """
    try:
        core.suspend_card(db, card_id)
        output_json({"card_id": card_id, "suspended": True})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("unsuspend")
def unsuspend(
    card_id: int = typer.Argument(..., help="Card ID to unsuspend"),
    db: Path = DB_DEFAULT,
) -> None:
    """Unsuspend a card (include in reviews again).

    Example:
        spacedrep card unsuspend 42
    """
    try:
        core.unsuspend_card(db, card_id)
        output_json({"card_id": card_id, "suspended": False})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
