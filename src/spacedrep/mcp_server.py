"""MCP server for spacedrep — exposes core functions as MCP tools.

Peer of the CLI: both call core.py, neither knows about the other.
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from spacedrep import core
from spacedrep.models import BulkCardInput, ReviewInput

mcp = FastMCP("spacedrep")

_DB_DEFAULT = "./collection.anki21"


def _db_path() -> Path:
    """Resolve database path from env at call time (not import time).

    This matters because the MCP server is long-running — the env var or
    working directory could change between tool invocations.
    """
    return Path(os.environ.get("SPACEDREP_DB", _DB_DEFAULT))


def _handle_errors(fn: Any) -> Any:  # noqa: ANN401
    """Wrap a tool function to catch errors and raise ToolError with structured JSON.

    SpacedrepError → structured JSON with error code, message, suggestion.
    Any other exception → generic internal error so agents never see raw tracebacks.
    """
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
        except ToolError:
            raise
        except Exception as e:
            error_data = {
                "error": "internal_error",
                "message": str(e),
                "suggestion": "This is an unexpected error. Check the database and try again.",
            }
            raise ToolError(_json.dumps(error_data, default=str)) from e

    return wrapper


def _serialize(data: BaseModel | dict[str, object]) -> dict[str, object]:
    """Serialize a Pydantic model or dict to a JSON-safe dict."""
    if isinstance(data, BaseModel):
        return data.model_dump()
    return data


def _or_none(val: str) -> str | None:
    """Convert empty string sentinel to None."""
    return val.strip() or None


def _parse_tags(tags: str) -> list[str] | None:
    """Parse space-separated tags string into a list, or None if empty."""
    if not tags:
        return None
    return tags.split()


def _validate_file_path(path_str: str, *, must_exist: bool = False) -> Path:
    """Resolve and validate a file path from agent input.

    Defends against path traversal: resolves to absolute, rejects paths
    containing '..' components or targeting sensitive dotfile directories.
    """
    resolved = Path(path_str).resolve()

    # Block paths with '..' that could escape intended directories
    if ".." in Path(path_str).parts:
        raise core.SpacedrepError(
            error_code="invalid_path",
            message=f"Path must not contain '..': {path_str}",
            suggestion="Use an absolute path or a simple relative path.",
        )

    # Block sensitive dotfile directories
    _sensitive = {".ssh", ".gnupg", ".aws", ".config"}
    if any(part in _sensitive for part in resolved.parts):
        raise core.SpacedrepError(
            error_code="invalid_path",
            message=f"Path targets a sensitive directory: {path_str}",
            suggestion="Use a path outside of sensitive dotfile directories.",
        )

    if must_exist and not resolved.exists():
        raise core.SpacedrepError(
            error_code="file_not_found",
            message=f"File not found: {resolved}",
            suggestion="Check the file path and try again.",
        )

    return resolved


# ---------------------------------------------------------------------------
# Card tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def add_card(
    question: str,
    answer: str,
    deck: Annotated[str, Field(description="Deck name (created if new)")] = "Default",
    tags: Annotated[str, Field(description="Space-separated tag names")] = "",
) -> dict[str, Any]:
    """Add a new flashcard. Re-adding the same question to the same deck updates
    the existing card instead of creating a duplicate.

    Dedup matches the question value used at note *creation* — if you
    later edit the Question via update_card and then re-add with the new
    text, you will get a separate note, not an update.

    Raises cross_model_collision if a reversed note with the same
    (question, deck) already exists; delete that card first to convert
    it to basic.

    Tags are space-separated. Use :: for hierarchy (e.g. 'foundations::rag').
    Returns the card ID, deck name, and was_update flag."""
    return core.add_card(_db_path(), question, answer, deck=deck, tags=tags)


@mcp.tool()
@_handle_errors
def add_reversed_card(
    question: str,
    answer: str,
    deck: Annotated[str, Field(description="Deck name (created if new)")] = "Default",
    tags: Annotated[str, Field(description="Space-separated tag names")] = "",
) -> dict[str, Any]:
    """Add a reversed card pair. Creates 2 cards from 1 shared note:
    one Q→A and one A→Q. Use for vocabulary or any concept where you
    want to be quizzed in both directions.

    Re-adding with the same (question, deck) updates the existing note
    in place and preserves review history on both cards. Dedup matches
    the question value used at note *creation* — if you later edit the
    Question via update_card and then re-add with the new text, you will
    get a separate note, not an update.

    To edit just one side, use update_card on the specific card_id — it
    is template-aware and edits the correct card's front/back.

    Raises cross_model_collision if a basic note with the same
    (question, deck) already exists; delete that card first to convert
    it to reversed.

    Returns note_id, card_ids (both directions), card_count (=2), deck."""
    return _serialize(core.add_reversed_card(_db_path(), question, answer, deck=deck, tags=tags))


@mcp.tool()
@_handle_errors
def add_cards_bulk(
    cards_json: Annotated[
        str,
        Field(
            description="JSON array of card objects. "
            'Basic: {"question":"...","answer":"...","deck":"...","tags":"..."}. '
            'Cloze: {"question":"{{c1::...}}","type":"cloze","deck":"...","tags":"..."}. '
            'Reversed: {"question":"...","answer":"...","type":"reversed",'
            '"deck":"...","tags":"..."} — creates 2 cards (Q→A and A→Q).'
        ),
    ],
) -> dict[str, Any]:
    """Add multiple flashcards in one transaction. Each object has: question,
    answer, deck (optional), tags (optional), type (optional: 'basic',
    'cloze', or 'reversed'). When type='cloze', question contains cloze
    text and answer is ignored. When type='reversed', two cards are
    created from one shared note (Q→A and A→Q).

    A cross_model_collision error (basic ↔ reversed with the same
    (question, deck)) aborts the entire batch — no partial inserts.
    Intra-batch collisions are caught too. The error includes
    `batch_index` (0-based) of the failing item, so the caller can
    pinpoint which entry collided without re-running."""
    from pydantic import TypeAdapter, ValidationError

    try:
        cards = TypeAdapter(list[BulkCardInput]).validate_json(cards_json)
    except ValidationError as e:
        raise core.BulkInputError(str(e)) from e
    return _serialize(core.add_cards_bulk(_db_path(), cards))


@mcp.tool()
@_handle_errors
def add_cloze_note(
    text: Annotated[
        str,
        Field(
            description="Cloze text with {{c1::answer}} syntax. "
            "Each cloze number creates one card. "
            "Example: '{{c1::Ottawa}} is capital of {{c2::Canada}}' creates 2 cards."
        ),
    ],
    deck: Annotated[str, Field(description="Deck name (created if new)")] = "Default",
    tags: Annotated[str, Field(description="Space-separated tag names")] = "",
) -> dict[str, Any]:
    """Add a cloze deletion note that expands into multiple flashcards.
    Use {{c1::answer}} syntax. Each cloze number creates one card.
    Example: '{{c1::Ottawa}} is capital of {{c2::Canada}}' creates 2 cards.

    Dedup keys on the exact cloze text. To edit an existing cloze note,
    use update_cloze_note — re-adding with different text creates a new
    note and leaves old cards as orphans."""
    return _serialize(core.add_cloze_note(_db_path(), text, deck=deck, tags=tags))


@mcp.tool()
@_handle_errors
def update_cloze_note(
    card_id: Annotated[int, Field(description="Any card ID from the cloze note to update")],
    text: Annotated[
        str,
        Field(
            description="New cloze text with {{c1::answer}} syntax. "
            "Cards for removed cloze numbers are deleted. "
            "Cards for existing ordinals keep their review history."
        ),
    ],
    tags: Annotated[
        str, Field(description="New space-separated tags (empty to keep existing)")
    ] = "",
) -> dict[str, Any]:
    """Update a cloze note's text by providing any card ID from it. Cards for
    ordinals that still exist keep their FSRS state and review history. New
    ordinals get new cards. Removed ordinals are deleted."""
    return _serialize(core.update_cloze_note(_db_path(), card_id, text, tags=_or_none(tags)))


@mcp.tool()
@_handle_errors
def get_next_card(
    deck: Annotated[str, Field(description="Deck name to filter by (includes :: sub-decks)")] = "",
    tags: Annotated[str, Field(description="Space-separated tags (OR logic, matches any)")] = "",
    state: Annotated[str, Field(description="Filter: new, learning, review, or relearning")] = "",
    search: Annotated[
        str, Field(description="Search text in question, answer, and extra fields")
    ] = "",
    due_before: Annotated[
        str, Field(description="Only cards due before this datetime (ISO format)")
    ] = "",
) -> dict[str, Any]:
    """Get the next flashcard due for review. Use at the start of a study session.
    Filter by deck name (includes :: sub-decks), space-separated tags, state, or
    search text. Tag filter matches the tag and all children (e.g. 'foundations'
    matches 'foundations::rag'). Returns card details or a message if no cards are due."""
    result = core.get_next_card(
        _db_path(),
        deck=_or_none(deck),
        tags=_parse_tags(tags),
        state=_or_none(state),
        search=_or_none(search),
        due_before=_or_none(due_before),
    )
    if result is None:
        next_due = core.get_next_due_time(_db_path())
        return {"card_id": None, "message": "No cards due", "next_due": next_due}
    return _serialize(result)


@mcp.tool()
@_handle_errors
def list_cards(
    deck: Annotated[str, Field(description="Deck name to filter by (includes :: sub-decks)")] = "",
    tags: Annotated[str, Field(description="Space-separated tags (OR logic, matches any)")] = "",
    state: Annotated[str, Field(description="Filter: new, learning, review, or relearning")] = "",
    search: Annotated[
        str, Field(description="Search text in question, answer, and extra fields")
    ] = "",
    leeches_only: Annotated[bool, Field(description="Only show leech cards (8+ lapses)")] = False,
    suspended: Annotated[
        str, Field(description="Filter by suspended: 'true', 'false', or '' for all")
    ] = "",
    due_before: Annotated[
        str, Field(description="Cards due before this datetime (ISO format)")
    ] = "",
    due_after: Annotated[str, Field(description="Cards due after this datetime (ISO format)")] = "",
    created_before: Annotated[
        str, Field(description="Cards created before this datetime (ISO format)")
    ] = "",
    created_after: Annotated[
        str, Field(description="Cards created after this datetime (ISO format)")
    ] = "",
    reviewed_before: Annotated[
        str, Field(description="Cards last reviewed before this datetime (ISO format)")
    ] = "",
    reviewed_after: Annotated[
        str, Field(description="Cards last reviewed after this datetime (ISO format)")
    ] = "",
    min_difficulty: Annotated[
        float, Field(description="Minimum difficulty (0-10). Use -1 for no filter.")
    ] = -1.0,
    max_difficulty: Annotated[
        float, Field(description="Maximum difficulty (0-10). Use -1 for no filter.")
    ] = -1.0,
    min_stability: Annotated[
        float, Field(description="Minimum stability in days. Use -1 for no filter.")
    ] = -1.0,
    max_stability: Annotated[
        float, Field(description="Maximum stability in days. Use -1 for no filter.")
    ] = -1.0,
    min_retrievability: Annotated[
        float, Field(description="Minimum retrievability (0-1). Use -1 for no filter.")
    ] = -1.0,
    max_retrievability: Annotated[
        float, Field(description="Maximum retrievability (0-1). Use -1 for no filter.")
    ] = -1.0,
    buried: Annotated[
        str, Field(description="Filter by buried: 'true', 'false', or '' for all")
    ] = "",
    limit: Annotated[int, Field(description="Max cards to return")] = 50,
    offset: Annotated[int, Field(description="Skip this many cards (for pagination)")] = 0,
) -> dict[str, Any]:
    """List flashcards with optional filters and pagination. Use to browse or
    search cards. Filter by deck (includes :: sub-decks), tags, state, search text,
    suspended status, date ranges, or leeches. Tag filter matches the tag
    and all children (e.g. 'foundations' matches 'foundations::rag')."""
    suspended_bool: bool | None = None
    if suspended == "true":
        suspended_bool = True
    elif suspended == "false":
        suspended_bool = False
    elif suspended:
        raise core.SpacedrepError(
            error_code="invalid_filter",
            message=f"Invalid value for suspended: {suspended!r}",
            suggestion="Use 'true', 'false', or '' (empty for no filter)",
            exit_code=2,
        )

    buried_bool: bool | None = None
    if buried == "true":
        buried_bool = True
    elif buried == "false":
        buried_bool = False
    elif buried:
        raise core.SpacedrepError(
            error_code="invalid_filter",
            message=f"Invalid value for buried: {buried!r}",
            suggestion="Use 'true', 'false', or '' (empty for no filter)",
            exit_code=2,
        )

    def _float_or_none(v: float) -> float | None:
        return v if v >= 0 else None

    return _serialize(
        core.list_cards(
            _db_path(),
            deck=_or_none(deck),
            tags=_parse_tags(tags),
            state=_or_none(state),
            search=_or_none(search),
            leeches_only=leeches_only,
            suspended=suspended_bool,
            due_before=_or_none(due_before),
            due_after=_or_none(due_after),
            created_before=_or_none(created_before),
            created_after=_or_none(created_after),
            reviewed_before=_or_none(reviewed_before),
            reviewed_after=_or_none(reviewed_after),
            min_difficulty=_float_or_none(min_difficulty),
            max_difficulty=_float_or_none(max_difficulty),
            min_stability=_float_or_none(min_stability),
            max_stability=_float_or_none(max_stability),
            min_retrievability=_float_or_none(min_retrievability),
            max_retrievability=_float_or_none(max_retrievability),
            buried=buried_bool,
            limit=limit,
            offset=offset,
        )
    )


@mcp.tool()
@_handle_errors
def get_card(card_id: int) -> dict[str, Any]:
    """Get full detail for a single flashcard by ID. Use to inspect a card's
    question, answer, FSRS scheduling state, and review history."""
    return _serialize(core.get_card_detail(_db_path(), card_id))


@mcp.tool()
@_handle_errors
def get_review_history(card_id: int) -> dict[str, Any]:
    """Get the review history for a card. Returns a list of review entries with
    rating, rating name, timestamp, and optional user answer/feedback. Use to
    analyze how a card has been performing over time."""
    return _serialize(core.get_review_history(_db_path(), card_id))


@mcp.tool()
@_handle_errors
def update_card(
    card_id: int,
    question: Annotated[str, Field(description="New question text")] = "",
    answer: Annotated[str, Field(description="New answer text")] = "",
    tags: Annotated[str, Field(description="New space-separated tags")] = "",
    deck: Annotated[str, Field(description="Move card to this deck")] = "",
) -> dict[str, Any]:
    """Update a flashcard's question, answer, tags, or deck. Only non-empty
    fields are changed. Provide at least one field to update.

    Field resolution is template-aware. For a reversed card, `question`
    updates the field that renders as *that* card's front (ord=0 writes
    the Question field; ord=1 writes the Answer field), so the edit
    shows up where you expect. Both cards of a reversed pair share one
    note, so edits propagate to both in the correct positions.

    `deck` is also template-aware: moving a reversed card's deck moves
    its sibling too (reversed pairs share one note, so splitting them
    across decks would be silently reverted on the next re-add). For
    single-template basic cards, `deck` moves only the named card.

    Cloze cards are rejected with an `update_cloze_card` error — use
    update_cloze_note to edit cloze text."""
    q = _or_none(question)
    a = _or_none(answer)
    t = _or_none(tags)
    d = _or_none(deck)
    if q is None and a is None and t is None and d is None:
        raise core.NoFieldsProvidedError()
    return _serialize(core.update_card(_db_path(), card_id, question=q, answer=a, tags=t, deck=d))


@mcp.tool()
@_handle_errors
def delete_card(
    card_id: int,
    dry_run: Annotated[bool, Field(description="Preview without making changes")] = False,
) -> dict[str, Any]:
    """Permanently delete a flashcard and its review history. Use dry_run=true
    to preview what would be deleted without making changes."""
    return core.delete_card(_db_path(), card_id, dry_run=dry_run)


@mcp.tool()
@_handle_errors
def suspend_card(
    card_id: int,
    dry_run: Annotated[bool, Field(description="Preview without making changes")] = False,
) -> dict[str, Any]:
    """Suspend a card to exclude it from reviews. Use for cards that are too
    hard or need revision. Use dry_run=true to preview."""
    return core.suspend_card(_db_path(), card_id, dry_run=dry_run)


@mcp.tool()
@_handle_errors
def unsuspend_card(
    card_id: int,
    dry_run: Annotated[bool, Field(description="Preview without making changes")] = False,
) -> dict[str, Any]:
    """Unsuspend a card to include it in reviews again. Use dry_run=true to preview."""
    return core.unsuspend_card(_db_path(), card_id, dry_run=dry_run)


@mcp.tool()
@_handle_errors
def bury_card(
    card_id: int,
    hours: Annotated[int, Field(description="Hours to bury the card for")] = 24,
) -> dict[str, Any]:
    """Temporarily exclude a card from reviews for a number of hours. Unlike
    suspend, buried cards automatically reappear when the time expires. Use for
    cards to revisit later in the session or tomorrow."""
    return core.bury_card(_db_path(), card_id, hours=hours)


@mcp.tool()
@_handle_errors
def unbury_card(card_id: int) -> dict[str, Any]:
    """Remove a card from buried status so it appears in reviews again."""
    return core.unbury_card(_db_path(), card_id)


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def submit_review(
    card_id: int,
    rating: Annotated[int, Field(description="1=again, 2=hard, 3=good, 4=easy")],
    answer: Annotated[str, Field(description="User's answer text")] = "",
    feedback: Annotated[str, Field(description="Feedback on the answer")] = "",
    session_id: Annotated[str, Field(description="Session ID for grouping reviews")] = "",
) -> dict[str, Any]:
    """Submit a review rating for a flashcard. The agent should determine the
    rating by comparing the user's answer to the card's correct answer — do not
    ask the user to pick a number. Rating: 1=again (wrong or no answer),
    2=hard (partially correct or missing key details), 3=good (correct),
    4=easy (use only if the user explicitly says it was easy). Always pass the
    user's original answer text in the answer field."""
    review_input = ReviewInput(
        card_id=card_id,
        rating=rating,
        user_answer=_or_none(answer),
        feedback=_or_none(feedback),
        session_id=_or_none(session_id),
    )
    return _serialize(core.submit_review(_db_path(), review_input))


@mcp.tool()
@_handle_errors
def preview_review(card_id: int) -> dict[str, Any]:
    """Preview what each of the 4 ratings (again/hard/good/easy) would produce
    for a card. Use before submitting a review to see scheduling outcomes."""
    return _serialize(core.preview_review(_db_path(), card_id))


# ---------------------------------------------------------------------------
# Deck tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def list_decks() -> dict[str, object]:
    """List all decks with their card counts and due counts."""
    decks = core.list_decks(_db_path())
    return {"decks": [d.model_dump() for d in decks], "total": len(decks)}


@mcp.tool()
@_handle_errors
def import_deck(
    apkg_path: Annotated[str, Field(description="Absolute path to .apkg file on disk")],
    force: Annotated[
        bool, Field(description="Replace existing database without safety check")
    ] = False,
) -> dict[str, Any]:
    """Open an Anki .apkg deck file as the working database. Replaces the
    current database. Use force=True to overwrite a database that already
    has cards."""
    validated = _validate_file_path(apkg_path, must_exist=True)
    return _serialize(core.open_deck(_db_path(), validated, force=force))


@mcp.tool()
@_handle_errors
def export_deck(
    output_path: Annotated[str, Field(description="Absolute path for the output .apkg file")],
) -> dict[str, Any]:
    """Save the entire collection as an Anki .apkg file. The .apkg contains
    all cards, decks, and scheduling state."""
    validated = _validate_file_path(output_path)
    result = core.save_deck(_db_path(), validated)
    return _serialize(result)


# ---------------------------------------------------------------------------
# Tag tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def list_tags() -> dict[str, object]:
    """List all unique tags in the database. Use to discover the tag
    taxonomy before filtering cards by tag. Tags use :: for hierarchy
    (e.g. 'foundations::rag::chunking')."""
    tags = core.list_tags(_db_path())
    return {"tags": tags, "total": len(tags)}


# ---------------------------------------------------------------------------
# Stats tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def get_due_count() -> dict[str, Any]:
    """Get count of cards currently due for review, broken down by state
    (new/learning/review). Use to check study workload."""
    return _serialize(core.get_due_count(_db_path()))


@mcp.tool()
@_handle_errors
def get_session_stats(
    session_id: Annotated[str, Field(description="Session ID from submit_review")],
) -> dict[str, Any]:
    """Get statistics for a specific review session: cards reviewed, rating
    breakdown, and accuracy."""
    return _serialize(core.get_session_stats(_db_path(), session_id))


@mcp.tool()
@_handle_errors
def get_overall_stats() -> dict[str, Any]:
    """Get overall database statistics: total cards, due count, maturity
    breakdown, and average retention."""
    return _serialize(core.get_overall_stats(_db_path()))


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


@mcp.tool()
@_handle_errors
def init_database() -> dict[str, Any]:
    """Initialize the spacedrep database. Run this before any other operation
    if the database doesn't exist yet. Safe to call multiple times (idempotent)."""
    return core.init_database(_db_path())


@mcp.tool()
@_handle_errors
def get_fsrs_status() -> dict[str, Any]:
    """Get current FSRS scheduler status: parameters, whether they're defaults
    or optimized, review count, and whether optimization is possible."""
    return _serialize(core.get_fsrs_status(_db_path()))


@mcp.tool()
@_handle_errors
def optimize_fsrs(
    reschedule: Annotated[
        bool, Field(description="Update all card schedules with new parameters")
    ] = False,
    dry_run: Annotated[bool, Field(description="Preview without making changes")] = False,
) -> dict[str, Any]:
    """Optimize FSRS scheduling parameters from review history. Requires 512+
    reviews. Set reschedule=true to update all card schedules with new parameters.
    Requires the optimizer extra (pip install spacedrep[optimizer]).
    Note: optimization can take 10-60 seconds with many reviews."""
    return _serialize(core.optimize_parameters(_db_path(), reschedule=reschedule, dry_run=dry_run))


def main() -> None:
    """Entry point for spacedrep-mcp script and python -m."""
    mcp.run()


if __name__ == "__main__":
    main()
