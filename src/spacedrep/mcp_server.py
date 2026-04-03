"""MCP server for spacedrep — exposes core functions as MCP tools.

Peer of the CLI: both call core.py, neither knows about the other.
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel

from spacedrep import core
from spacedrep.models import BulkCardInput, ReviewInput

mcp = FastMCP("spacedrep")

DB_PATH = Path(os.environ.get("SPACEDREP_DB", "./reviews.db"))


def _handle_errors(fn: Any) -> Any:  # noqa: ANN401
    """Wrap a tool function to catch SpacedrepError and raise ToolError."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        try:
            return fn(*args, **kwargs)
        except core.SpacedrepError as e:
            error_data = {
                "error": e.error_code,
                "message": e.message,
                "suggestion": e.suggestion,
                **e.extra,
            }
            raise ToolError(_json.dumps(error_data, default=str)) from e

    return wrapper


def _serialize(data: BaseModel | dict[str, object]) -> dict[str, object]:
    """Serialize a Pydantic model or dict to a JSON-safe dict."""
    if isinstance(data, BaseModel):
        return data.model_dump()
    return data


def _serialize_list(data: list[BaseModel]) -> dict[str, object]:
    """Serialize a list of Pydantic models to a JSON-safe dict."""
    items: list[dict[str, object]] = [d.model_dump() for d in data]
    return {"items": items}


def _or_none(val: str) -> str | None:
    """Convert empty string sentinel to None."""
    return val if val else None


def _parse_tags(tags: str) -> list[str] | None:
    """Parse comma-separated tags string into a list, or None if empty."""
    if not tags:
        return None
    return [t.strip() for t in tags.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Card tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def add_card(
    question: str,
    answer: str,
    deck: str = "Default",
    tags: str = "",
) -> dict[str, Any]:
    """Add a new flashcard. Use when the user wants to create a single card.
    Tags are comma-separated. Returns the new card ID and deck name."""
    return core.add_card(DB_PATH, question, answer, deck=deck, tags=tags)


@mcp.tool()
@_handle_errors
def add_cards_bulk(cards_json: str) -> dict[str, Any]:
    """Add multiple flashcards in one transaction. Pass a JSON array string of
    objects with keys: question, answer, deck (optional), tags (optional).
    Example: '[{"question":"Q1","answer":"A1","deck":"AWS"}]'"""
    from pydantic import TypeAdapter, ValidationError

    try:
        cards = TypeAdapter(list[BulkCardInput]).validate_json(cards_json)
    except ValidationError as e:
        raise core.BulkInputError(str(e)) from e
    return _serialize(core.add_cards_bulk(DB_PATH, cards))


@mcp.tool()
@_handle_errors
def get_next_card(
    deck: str = "",
    tags: str = "",
    state: str = "",
) -> dict[str, Any]:
    """Get the next flashcard due for review. Use at the start of a study session.
    Filter by deck name, comma-separated tags, or state (new/learning/review/relearning).
    Returns card details or a message if no cards are due."""
    result = core.get_next_card(
        DB_PATH,
        deck=_or_none(deck),
        tags=_parse_tags(tags),
        state=_or_none(state),
    )
    if result is None:
        next_due = core.get_next_due_time(DB_PATH)
        return {"card_id": None, "message": "No cards due", "next_due": next_due}
    return _serialize(result)


@mcp.tool()
@_handle_errors
def list_cards(
    deck: str = "",
    tags: str = "",
    state: str = "",
    leeches_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List flashcards with optional filters and pagination. Use to browse or
    search cards. Filter by deck, comma-separated tags, state, or leeches only."""
    return _serialize(
        core.list_cards(
            DB_PATH,
            deck=_or_none(deck),
            tags=_parse_tags(tags),
            state=_or_none(state),
            leeches_only=leeches_only,
            limit=limit,
            offset=offset,
        )
    )


@mcp.tool()
@_handle_errors
def get_card(card_id: int) -> dict[str, Any]:
    """Get full detail for a single flashcard by ID. Use to inspect a card's
    question, answer, FSRS scheduling state, and review history."""
    return _serialize(core.get_card_detail(DB_PATH, card_id))


@mcp.tool()
@_handle_errors
def update_card(
    card_id: int,
    question: str = "",
    answer: str = "",
    tags: str = "",
    deck: str = "",
) -> dict[str, Any]:
    """Update a flashcard's question, answer, tags, or deck. Only non-empty
    fields are changed. Provide at least one field to update."""
    q = _or_none(question)
    a = _or_none(answer)
    t = _or_none(tags)
    d = _or_none(deck)
    if q is None and a is None and t is None and d is None:
        raise core.NoFieldsProvidedError()
    return _serialize(core.update_card(DB_PATH, card_id, question=q, answer=a, tags=t, deck=d))


@mcp.tool()
@_handle_errors
def delete_card(card_id: int, dry_run: bool = False) -> dict[str, Any]:
    """Permanently delete a flashcard and its review history. Use dry_run=true
    to preview what would be deleted without making changes."""
    return core.delete_card(DB_PATH, card_id, dry_run=dry_run)


@mcp.tool()
@_handle_errors
def suspend_card(card_id: int, dry_run: bool = False) -> dict[str, Any]:
    """Suspend a card to exclude it from reviews. Use for cards that are too
    hard or need revision. Use dry_run=true to preview."""
    result = core.suspend_card(DB_PATH, card_id, dry_run=dry_run)
    if isinstance(result, bool):
        return {"card_id": card_id, "suspended": True}
    return result


@mcp.tool()
@_handle_errors
def unsuspend_card(card_id: int, dry_run: bool = False) -> dict[str, Any]:
    """Unsuspend a card to include it in reviews again. Use dry_run=true to preview."""
    result = core.unsuspend_card(DB_PATH, card_id, dry_run=dry_run)
    if isinstance(result, bool):
        return {"card_id": card_id, "suspended": False}
    return result


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def submit_review(
    card_id: int,
    rating: int,
    answer: str = "",
    feedback: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Submit a review rating for a flashcard. Rating: 1=again, 2=hard, 3=good,
    4=easy. Optionally include the user's answer text and a session ID for
    grouping reviews."""
    review_input = ReviewInput(
        card_id=card_id,
        rating=rating,
        user_answer=_or_none(answer),
        feedback=_or_none(feedback),
        session_id=_or_none(session_id),
    )
    return _serialize(core.submit_review(DB_PATH, review_input))


@mcp.tool()
@_handle_errors
def preview_review(card_id: int) -> dict[str, Any]:
    """Preview what each of the 4 ratings (again/hard/good/easy) would produce
    for a card. Use before submitting a review to see scheduling outcomes."""
    return _serialize(core.preview_review(DB_PATH, card_id))


# ---------------------------------------------------------------------------
# Deck tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def list_decks() -> dict[str, object]:
    """List all decks with their card counts and due counts."""
    return _serialize_list(list(core.list_decks(DB_PATH)))


@mcp.tool()
@_handle_errors
def import_deck(
    apkg_path: str,
    question_field: str = "",
    answer_field: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import an Anki .apkg deck file. Specify the file path on disk.
    Optionally set question_field and answer_field names if the deck uses
    non-standard field names. Use dry_run=true to preview without writing."""
    return _serialize(
        core.import_deck(
            DB_PATH,
            Path(apkg_path),
            _or_none(question_field),
            _or_none(answer_field),
            dry_run=dry_run,
        )
    )


@mcp.tool()
@_handle_errors
def export_deck(output_path: str, deck: str = "") -> dict[str, Any]:
    """Export flashcards to an Anki .apkg file. Optionally filter by deck name.
    If no deck specified, exports all cards."""
    count = core.export_deck(DB_PATH, Path(output_path), _or_none(deck))
    return {"exported": count, "file": output_path}


# ---------------------------------------------------------------------------
# Stats tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def get_due_count() -> dict[str, Any]:
    """Get count of cards currently due for review, broken down by state
    (new/learning/review). Use to check study workload."""
    return _serialize(core.get_due_count(DB_PATH))


@mcp.tool()
@_handle_errors
def get_session_stats(session_id: str) -> dict[str, Any]:
    """Get statistics for a specific review session: cards reviewed, rating
    breakdown, and accuracy."""
    return _serialize(core.get_session_stats(DB_PATH, session_id))


@mcp.tool()
@_handle_errors
def get_overall_stats() -> dict[str, Any]:
    """Get overall database statistics: total cards, due count, maturity
    breakdown, and average retention."""
    return _serialize(core.get_overall_stats(DB_PATH))


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def init_database() -> dict[str, Any]:
    """Initialize the spacedrep database. Run this before any other operation
    if the database doesn't exist yet. Safe to call multiple times (idempotent)."""
    return core.init_database(DB_PATH)


@mcp.tool()
@_handle_errors
def get_fsrs_status() -> dict[str, Any]:
    """Get current FSRS scheduler status: parameters, whether they're defaults
    or optimized, review count, and whether optimization is possible."""
    return _serialize(core.get_fsrs_status(DB_PATH))


@mcp.tool()
@_handle_errors
def optimize_fsrs(reschedule: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Optimize FSRS scheduling parameters from review history. Requires 512+
    reviews. Set reschedule=true to update all card schedules with new parameters.
    Requires the optimizer extra (pip install spacedrep[optimizer])."""
    return _serialize(core.optimize_parameters(DB_PATH, reschedule=reschedule, dry_run=dry_run))


def main() -> None:
    """Entry point for spacedrep-mcp script and python -m."""
    mcp.run()


if __name__ == "__main__":
    main()
