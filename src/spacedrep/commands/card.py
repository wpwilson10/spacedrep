"""Card management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json, output_quiet

card_app = typer.Typer(name="card", help="Card operations")

QUIET_OPT = typer.Option(False, "--quiet", "-q", help="Output bare values for piping")
DRY_RUN_OPT = typer.Option(
    False, "--dry-run", help="Preview what would happen without making changes"
)


def _parse_tags(tags: str | None) -> list[str] | None:
    """Parse space-separated tags string into a list, or None if empty."""
    if not tags:
        return None
    return tags.split()


@card_app.command("next")
def next_card(
    deck: str | None = typer.Option(None, help="Filter by deck (includes :: sub-decks)"),
    tags: str | None = typer.Option(None, help="Filter by space-separated tags"),
    state: str | None = typer.Option(
        None, help="Filter by state: new, learning, review, relearning"
    ),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Get the next due card for review.

    Example:
        spacedrep card next
        spacedrep card next --deck AWS --tags "s3 storage"
        spacedrep card next --state new
        spacedrep card next -q
    """
    try:
        tags_list = _parse_tags(tags)
        result = core.get_next_card(db, deck=deck, tags=tags_list, state=state)
        if result is None:
            if quiet:
                return
            next_due = core.get_next_due_time(db)
            output_json({"card_id": None, "message": "No cards due", "next_due": next_due})
        elif quiet:
            output_quiet(result.card_id)
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
    tags: str = typer.Option("", help="Space-separated tags"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Add a new flashcard.

    Example:
        spacedrep card add "Why eventual consistency?" "Availability tradeoff" --deck AWS --tags s3
        spacedrep card add "Q" "A" -q
    """
    try:
        result = core.add_card(db, question, answer, deck=deck, tags=tags)
        if quiet:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("add-bulk")
def add_bulk(
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Add multiple cards from JSON on stdin.

    Example:
        echo '[{"question":"Q1","answer":"A1","deck":"AWS"}]' | spacedrep card add-bulk
        echo '[{"question":"Q1","answer":"A1"}]' | spacedrep card add-bulk -q
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
        if quiet:
            output_quiet(result.created)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("list")
def list_cards(
    deck: str | None = typer.Option(None, help="Filter by deck (includes :: sub-decks)"),
    tags: str | None = typer.Option(None, help="Filter by space-separated tags"),
    state: str | None = typer.Option(
        None, help="Filter by state: new, learning, review, relearning"
    ),
    leeches: bool = typer.Option(False, "--leeches", help="Show only leech cards"),
    limit: int = typer.Option(50, help="Max cards to return"),
    offset: int = typer.Option(0, help="Offset for pagination"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """List cards with optional filters.

    Example:
        spacedrep card list --deck AWS --tags s3 --limit 10
        spacedrep card list -q | xargs -I{} spacedrep card get {}
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
        if quiet:
            output_quiet([c.card_id for c in result.cards])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("tags")
def list_tags(
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """List all unique tags in the database.

    Example:
        spacedrep card tags
        spacedrep card tags -q
    """
    try:
        tags = core.list_tags(db)
        if quiet:
            for tag in tags:
                output_quiet(tag)
        else:
            output_json({"tags": tags, "count": len(tags)})
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("get")
def get_card(
    card_id: int = typer.Argument(..., help="Card ID"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Get full card detail by ID.

    Example:
        spacedrep card get 1
    """
    try:
        result = core.get_card_detail(db, card_id)
        if quiet:
            output_quiet(result.card_id)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("delete")
def delete_card(
    card_id: int = typer.Argument(..., help="Card ID to delete"),
    dry_run: bool = DRY_RUN_OPT,
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Delete a card and all its review history.

    Example:
        spacedrep card delete 42
        spacedrep card delete 42 --dry-run
    """
    try:
        result = core.delete_card(db, card_id, dry_run=dry_run)
        if quiet:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("update")
def update_card(
    card_id: int = typer.Argument(..., help="Card ID to update"),
    question: str | None = typer.Option(None, help="New question text"),
    answer: str | None = typer.Option(None, help="New answer text"),
    tags: str | None = typer.Option(None, help="New space-separated tags"),
    deck: str | None = typer.Option(None, help="Move to deck"),
    quiet: bool = QUIET_OPT,
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
        if quiet:
            output_quiet(result.card_id)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("suspend")
def suspend(
    card_id: int = typer.Argument(..., help="Card ID to suspend"),
    dry_run: bool = DRY_RUN_OPT,
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Suspend a card (exclude from reviews).

    Example:
        spacedrep card suspend 42
        spacedrep card suspend 42 --dry-run
    """
    try:
        result = core.suspend_card(db, card_id, dry_run=dry_run)
        if quiet and not dry_run:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("unsuspend")
def unsuspend(
    card_id: int = typer.Argument(..., help="Card ID to unsuspend"),
    dry_run: bool = DRY_RUN_OPT,
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Unsuspend a card (include in reviews again).

    Example:
        spacedrep card unsuspend 42
        spacedrep card unsuspend 42 --dry-run
    """
    try:
        result = core.unsuspend_card(db, card_id, dry_run=dry_run)
        if quiet and not dry_run:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
