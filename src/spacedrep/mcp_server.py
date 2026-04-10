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

_DB_DEFAULT = "./reviews.db"


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
    """Add a new flashcard. Use when the user wants to create a single card.
    Tags are space-separated. Use :: for hierarchy (e.g. 'foundations::rag').
    Returns the new card ID and deck name."""
    return core.add_card(_db_path(), question, answer, deck=deck, tags=tags)


@mcp.tool()
@_handle_errors
def add_cards_bulk(
    cards_json: Annotated[
        str,
        Field(
            description="JSON array of card objects. "
            'Basic: {"question":"...","answer":"...","deck":"...","tags":"..."}. '
            'Cloze: {"question":"{{c1::...}}","type":"cloze","deck":"...","tags":"..."}.'
        ),
    ],
) -> dict[str, Any]:
    """Add multiple flashcards in one transaction. Each object has: question,
    answer, deck (optional), tags (optional), type (optional: 'basic' or 'cloze').
    When type='cloze', question contains cloze text and answer is ignored."""
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
    To edit an existing cloze note, use update_cloze_note instead -- re-adding
    changed text creates a new note and leaves old cards as orphans."""
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
    source: Annotated[str, Field(description="Filter by source: apkg, manual, or generated")] = "",
    limit: Annotated[int, Field(description="Max cards to return")] = 50,
    offset: Annotated[int, Field(description="Skip this many cards (for pagination)")] = 0,
) -> dict[str, Any]:
    """List flashcards with optional filters and pagination. Use to browse or
    search cards. Filter by deck (includes :: sub-decks), tags, state, search text,
    suspended status, source, or leeches. Tag filter matches the tag and all
    children (e.g. 'foundations' matches 'foundations::rag')."""
    suspended_bool: bool | None = None
    if suspended == "true":
        suspended_bool = True
    elif suspended == "false":
        suspended_bool = False
    return _serialize(
        core.list_cards(
            _db_path(),
            deck=_or_none(deck),
            tags=_parse_tags(tags),
            state=_or_none(state),
            search=_or_none(search),
            leeches_only=leeches_only,
            suspended=suspended_bool,
            source=_or_none(source),
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
def update_card(
    card_id: int,
    question: Annotated[str, Field(description="New question text")] = "",
    answer: Annotated[str, Field(description="New answer text")] = "",
    tags: Annotated[str, Field(description="New space-separated tags")] = "",
    deck: Annotated[str, Field(description="Move card to this deck")] = "",
) -> dict[str, Any]:
    """Update a flashcard's question, answer, tags, or deck. Only non-empty
    fields are changed. Provide at least one field to update."""
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
    return {"decks": [d.model_dump() for d in decks], "count": len(decks)}


@mcp.tool()
@_handle_errors
def import_deck(
    apkg_path: Annotated[str, Field(description="Absolute path to .apkg file on disk")],
    question_field: Annotated[str, Field(description="Question field name if non-standard")] = "",
    answer_field: Annotated[str, Field(description="Answer field name if non-standard")] = "",
    dry_run: Annotated[bool, Field(description="Preview without making changes")] = False,
) -> dict[str, Any]:
    """Import an Anki .apkg deck file. Specify the absolute file path on disk.
    Optionally set question_field and answer_field names if the deck uses
    non-standard field names. Use dry_run=true to preview without writing."""
    validated = _validate_file_path(apkg_path, must_exist=True)
    return _serialize(
        core.import_deck(
            _db_path(),
            validated,
            _or_none(question_field),
            _or_none(answer_field),
            dry_run=dry_run,
        )
    )


@mcp.tool()
@_handle_errors
def export_deck(
    output_path: Annotated[str, Field(description="Absolute path for the output .apkg file")],
    deck: Annotated[str, Field(description="Deck name to export (all if empty)")] = "",
) -> dict[str, Any]:
    """Export flashcards to an Anki .apkg file. Optionally filter by deck name.
    If no deck specified, exports all cards."""
    validated = _validate_file_path(output_path)
    count = core.export_deck(_db_path(), validated, _or_none(deck))
    return {"exported": count, "file": str(validated)}


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
    return {"tags": tags, "count": len(tags)}


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
