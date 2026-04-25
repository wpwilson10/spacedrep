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
    search: str | None = typer.Option(
        None, "--search", "-s", help="Search text in question, answer, and extra fields"
    ),
    due_before: str | None = typer.Option(
        None, "--due-before", help="Only cards due before this datetime (ISO format)"
    ),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Get the next due card for review.

    Example:
        spacedrep card next
        spacedrep card next --deck AWS --tags "s3 storage"
        spacedrep card next --state new
        spacedrep card next --search Lambda
        spacedrep card next -q
    """
    try:
        tags_list = _parse_tags(tags)
        result = core.get_next_card(
            db, deck=deck, tags=tags_list, state=state, search=search, due_before=due_before
        )
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

    Dedup matches the question used at creation — editing via `card update`
    and re-adding with the new text creates a separate note. Raises
    cross_model_collision if a reversed note with the same (question, deck)
    already exists.

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
    search: str | None = typer.Option(
        None, "--search", "-s", help="Search text in question, answer, and extra fields"
    ),
    leeches: bool = typer.Option(False, "--leeches", help="Show only leech cards"),
    suspended: bool | None = typer.Option(None, help="Filter by suspended status"),
    buried: bool | None = typer.Option(None, help="Filter by buried status"),
    due_before: str | None = typer.Option(
        None, "--due-before", help="Cards due before this datetime (ISO format)"
    ),
    due_after: str | None = typer.Option(
        None, "--due-after", help="Cards due after this datetime (ISO format)"
    ),
    created_before: str | None = typer.Option(
        None, "--created-before", help="Cards created before this datetime (ISO format)"
    ),
    created_after: str | None = typer.Option(
        None, "--created-after", help="Cards created after this datetime (ISO format)"
    ),
    reviewed_before: str | None = typer.Option(
        None, "--reviewed-before", help="Cards last reviewed before this datetime (ISO format)"
    ),
    reviewed_after: str | None = typer.Option(
        None, "--reviewed-after", help="Cards last reviewed after this datetime (ISO format)"
    ),
    min_difficulty: float | None = typer.Option(
        None, "--min-difficulty", help="Minimum difficulty (0-10)"
    ),
    max_difficulty: float | None = typer.Option(
        None, "--max-difficulty", help="Maximum difficulty (0-10)"
    ),
    min_stability: float | None = typer.Option(
        None, "--min-stability", help="Minimum stability in days"
    ),
    max_stability: float | None = typer.Option(
        None, "--max-stability", help="Maximum stability in days"
    ),
    min_retrievability: float | None = typer.Option(
        None, "--min-retrievability", help="Minimum retrievability (0-1)"
    ),
    max_retrievability: float | None = typer.Option(
        None, "--max-retrievability", help="Maximum retrievability (0-1)"
    ),
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
            search=search,
            suspended=suspended,
            due_before=due_before,
            due_after=due_after,
            created_before=created_before,
            created_after=created_after,
            reviewed_before=reviewed_before,
            reviewed_after=reviewed_after,
            min_difficulty=min_difficulty,
            max_difficulty=max_difficulty,
            min_stability=min_stability,
            max_stability=max_stability,
            min_retrievability=min_retrievability,
            max_retrievability=max_retrievability,
            buried=buried,
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

    Field resolution is template-aware: `--question` edits whichever
    note field renders as *this* card's front. For a reversed pair
    both cards share one note, so edits also update the other card's
    corresponding side. `--deck` on a reversed card moves both siblings
    (splitting them would be silently reverted on the next re-add).
    Cloze cards are rejected — use `card update-cloze` instead.

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


@card_app.command("bury")
def bury(
    card_id: int = typer.Argument(..., help="Card ID to bury"),
    hours: int = typer.Option(24, "--hours", help="Hours to bury the card for"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Temporarily exclude a card from reviews.

    Example:
        spacedrep card bury 42 --hours 4
    """
    try:
        result = core.bury_card(db, card_id, hours=hours)
        if quiet:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("unbury")
def unbury(
    card_id: int = typer.Argument(..., help="Card ID to unbury"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Remove a card from buried status.

    Example:
        spacedrep card unbury 42
    """
    try:
        result = core.unbury_card(db, card_id)
        if quiet:
            output_quiet(result["card_id"])
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("add-reversed")
def add_reversed(
    question: str = typer.Argument(..., help="The card question"),
    answer: str = typer.Argument(..., help="The card answer"),
    deck: str = typer.Option("Default", help="Deck name"),
    tags: str = typer.Option("", help="Space-separated tags"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Add a reversed card pair: 2 cards from 1 note (Q→A and A→Q).

    Re-running with the same (question, deck) updates the existing note
    in place — both cards reflect the new answer, review history is
    preserved. Dedup matches the question used at creation: editing the
    Question via `card update` and then re-running add-reversed with the
    new text creates a separate note.

    To edit the text later, use `card update` (template-aware: edits the
    side of the specific card you name).

    Raises cross_model_collision if a basic note with the same
    (question, deck) already exists; delete that card first.

    Example:
        spacedrep card add-reversed "Capital of France" "Paris" --deck geo
    """
    try:
        result = core.add_reversed_card(db, question, answer, deck=deck, tags=tags)
        if quiet:
            output_quiet(result.card_ids)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("add-cloze")
def add_cloze(
    text: str = typer.Argument(..., help="Cloze text with {{c1::answer}} syntax"),
    deck: str = typer.Option("Default", help="Deck name"),
    tags: str = typer.Option("", help="Space-separated tags"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Add a cloze deletion note that expands into multiple flashcards.

    Dedup keys on the exact cloze text. Use `card update-cloze` to edit
    an existing cloze note; re-running add-cloze with different text
    creates a new note.

    Example:
        spacedrep card add-cloze "{{c1::Ottawa}} is the capital of {{c2::Canada}}" --deck Geo
    """
    try:
        result = core.add_cloze_note(db, text, deck=deck, tags=tags)
        if quiet:
            output_quiet(result.card_ids)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("history")
def history(
    card_id: int = typer.Argument(..., help="Card ID"),
    db: Path = DB_DEFAULT,
) -> None:
    """Show review history for a card.

    Example:
        spacedrep card history 1
    """
    try:
        result = core.get_review_history(db, card_id)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@card_app.command("update-cloze")
def update_cloze(
    card_id: int = typer.Argument(..., help="Any card ID from the cloze note"),
    text: str = typer.Argument(..., help="New cloze text with {{c1::answer}} syntax"),
    tags: str | None = typer.Option(None, help="New space-separated tags (omit to keep existing)"),
    quiet: bool = QUIET_OPT,
    db: Path = DB_DEFAULT,
) -> None:
    """Update a cloze note by providing any card ID from it.

    Example:
        spacedrep card update-cloze 42 "{{c1::Ottawa}} is in {{c2::Canada}}, {{c3::North America}}"
    """
    try:
        result = core.update_cloze_note(db, card_id, text, tags=tags)
        if quiet:
            output_quiet(result.card_ids)
        else:
            output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
