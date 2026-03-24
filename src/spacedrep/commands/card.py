"""Card management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

card_app = typer.Typer(name="card", help="Card operations")


def _parse_tags(tags: str | None) -> list[str] | None:
    """Parse comma-separated tags string into a list, or None if empty."""
    if not tags:
        return None
    return [t.strip() for t in tags.split(",") if t.strip()]


@card_app.command("next")
def next_card(
    deck: str | None = typer.Option(None, help="Filter by deck name"),
    tags: str | None = typer.Option(None, help="Filter by comma-separated tags"),
    state: str | None = typer.Option(
        None, help="Filter by state: new, learning, review, relearning"
    ),
    db: Path = DB_DEFAULT,
) -> None:
    """Get the next due card for review.

    Example:
        spacedrep card next
        spacedrep card next --deck AWS --tags s3,storage
        spacedrep card next --state new
    """
    try:
        tags_list = _parse_tags(tags)
        result = core.get_next_card(db, deck=deck, tags=tags_list, state=state)
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


@card_app.command("add-bulk")
def add_bulk(
    db: Path = DB_DEFAULT,
) -> None:
    """Add multiple cards from JSON on stdin.

    Example:
        echo '[{"question":"Q1","answer":"A1","deck":"AWS"}]' | spacedrep card add-bulk
    """
    import sys as _sys

    from pydantic import TypeAdapter, ValidationError

    from spacedrep.models import BulkCardInput

    raw = _sys.stdin.read()
    try:
        cards = TypeAdapter(list[BulkCardInput]).validate_json(raw)
    except ValidationError as e:
        err = core.BulkInputError(str(e))
        output_error(err)
        raise typer.Exit(code=err.exit_code) from None

    try:
        result = core.add_cards_bulk(db, cards)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("list")
def list_cards(
    deck: str | None = typer.Option(None, help="Filter by deck name"),
    tags: str | None = typer.Option(None, help="Filter by comma-separated tags"),
    state: str | None = typer.Option(
        None, help="Filter by state: new, learning, review, relearning"
    ),
    leeches: bool = typer.Option(False, "--leeches", help="Show only leech cards"),
    limit: int = typer.Option(50, help="Max cards to return"),
    offset: int = typer.Option(0, help="Offset for pagination"),
    db: Path = DB_DEFAULT,
) -> None:
    """List cards with optional filters.

    Example:
        spacedrep card list --deck AWS --tags s3 --limit 10
    """
    try:
        tags_list = _parse_tags(tags)
        result = core.list_cards(
            db,
            deck=deck,
            tags=tags_list,
            state=state,
            leeches_only=leeches,
            limit=limit,
            offset=offset,
        )
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("get")
def get_card(
    card_id: int = typer.Argument(..., help="Card ID"),
    db: Path = DB_DEFAULT,
) -> None:
    """Get full card detail by ID.

    Example:
        spacedrep card get 1
    """
    try:
        result = core.get_card_detail(db, card_id)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("delete")
def delete_card(
    card_id: int = typer.Argument(..., help="Card ID to delete"),
    db: Path = DB_DEFAULT,
) -> None:
    """Delete a card and all its review history.

    Example:
        spacedrep card delete 42
    """
    try:
        result = core.delete_card(db, card_id)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("update")
def update_card(
    card_id: int = typer.Argument(..., help="Card ID to update"),
    question: str | None = typer.Option(None, help="New question text"),
    answer: str | None = typer.Option(None, help="New answer text"),
    tags: str | None = typer.Option(None, help="New comma-separated tags"),
    deck: str | None = typer.Option(None, help="Move to deck"),
    db: Path = DB_DEFAULT,
) -> None:
    """Update a card's question, answer, tags, or deck.

    Example:
        spacedrep card update 1 --question "Updated question" --deck DSA
    """
    if question is None and answer is None and tags is None and deck is None:
        err = core.NoFieldsProvidedError()
        output_error(err)
        raise typer.Exit(code=err.exit_code) from None
    try:
        result = core.update_card(
            db, card_id, question=question, answer=answer, tags=tags, deck=deck
        )
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
