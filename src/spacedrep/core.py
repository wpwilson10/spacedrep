"""Business logic orchestration layer.

Both CLI and (future) MCP call these functions. No CLI or MCP dependencies.
"""

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spacedrep import db, fsrs_engine
from spacedrep.models import (
    BulkAddResult,
    BulkCardInput,
    CardDetail,
    CardDue,
    CardListResult,
    CardRecord,
    CardSource,
    ClozeAddResult,
    DeckInfo,
    DueCount,
    FsrsStatus,
    ImportResult,
    OptimizeResult,
    OverallStats,
    ReviewHistory,
    ReviewInput,
    ReviewPreview,
    ReviewResult,
    SessionStats,
)


class SpacedrepError(Exception):
    """Base error with structured fields for JSON output."""

    def __init__(
        self,
        error_code: str,
        message: str,
        suggestion: str = "",
        exit_code: int = 1,
        **extra: str | int | None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.suggestion = suggestion
        self.exit_code = exit_code
        self.extra = extra


class CardNotFoundError(SpacedrepError):
    def __init__(self, card_id: int) -> None:
        super().__init__(
            error_code="card_not_found",
            message=f"Card {card_id} not found",
            suggestion="Check the card ID with 'spacedrep card list'",
            exit_code=3,
            card_id=card_id,
        )


class DatabaseNotFoundError(SpacedrepError):
    def __init__(self, db_path: Path) -> None:
        super().__init__(
            error_code="database_not_found",
            message=f"Database not found: {db_path}",
            suggestion="Run 'spacedrep db init' first",
            exit_code=3,
        )


class InvalidRatingError(SpacedrepError):
    def __init__(self, rating: str) -> None:
        super().__init__(
            error_code="invalid_rating",
            message=f"Invalid rating: {rating}. Must be 1-4 (again/hard/good/easy)",
            suggestion="Use: 1=again, 2=hard, 3=good, 4=easy",
            exit_code=2,
        )


class InvalidStateError(SpacedrepError):
    def __init__(self, state: str) -> None:
        super().__init__(
            error_code="invalid_state",
            message=f"Invalid state: {state}. Must be one of: new, learning, review, relearning",
            suggestion="Use: new, learning, review, or relearning",
            exit_code=2,
        )


class NoFieldsProvidedError(SpacedrepError):
    def __init__(self) -> None:
        super().__init__(
            error_code="no_fields",
            message="Provide at least one field to update: question, answer, tags, or deck",
            suggestion="Set at least one of: question, answer, tags, deck",
            exit_code=2,
        )


class ApkgImportError(SpacedrepError):
    def __init__(self, message: str) -> None:
        super().__init__(
            error_code="import_error",
            message=message,
            suggestion="Check the file path and format (.apkg)",
            exit_code=1,
        )


class BulkInputError(SpacedrepError):
    def __init__(self, message: str) -> None:
        super().__init__(
            error_code="bulk_input_error",
            message=message,
            suggestion="Input must be a JSON array of {question, answer, deck?, tags?}",
            exit_code=2,
        )


class NoClozeMarkersError(SpacedrepError):
    def __init__(self) -> None:
        super().__init__(
            error_code="no_cloze_markers",
            message=(
                "Text must contain at least one cloze marker"
                " with non-empty content, e.g. {{c1::answer}}"
            ),
            suggestion="Use {{c1::answer}} syntax. Content cannot be empty.",
            exit_code=2,
        )


class NotAClozeNoteError(SpacedrepError):
    def __init__(self, card_id: int) -> None:
        super().__init__(
            error_code="not_a_cloze_note",
            message=f"Card {card_id} is not part of a cloze note",
            suggestion="Use update_card for standalone cards",
            exit_code=2,
            card_id=card_id,
        )


class OptimizerNotInstalledError(SpacedrepError):
    def __init__(self) -> None:
        super().__init__(
            error_code="optimizer_not_installed",
            message="FSRS optimizer requires the 'optimizer' extra",
            suggestion="Install with: pip install spacedrep[optimizer]",
            exit_code=1,
        )


class InvalidBuryDurationError(SpacedrepError):
    def __init__(self, hours: int) -> None:
        super().__init__(
            error_code="invalid_bury_duration",
            message=f"Bury duration must be at least 1 hour, got {hours}",
            suggestion="Use a positive number of hours (e.g. 24)",
            exit_code=2,
            hours=hours,
        )


class EmptyFieldError(SpacedrepError):
    def __init__(self, field: str) -> None:
        super().__init__(
            error_code="empty_field",
            message=f"Card {field} cannot be empty or whitespace-only",
            suggestion=f"Provide a non-empty {field} for the card",
            exit_code=2,
            field=field,
        )


class CardSuspendedError(SpacedrepError):
    def __init__(self, card_id: int) -> None:
        super().__init__(
            error_code="card_suspended",
            message=f"Card {card_id} is suspended — reviews have no effect",
            suggestion="Unsuspend the card first with unsuspend_card, then review it",
            exit_code=2,
            card_id=card_id,
        )


# Concurrency note: _params_loaded guards a one-time load of FSRS parameters
# from the database into the module-level scheduler. This is safe because
# FastMCP serializes tool calls via asyncio (single-threaded). If concurrency
# model changes, this must become a lock-protected initialization.
_params_loaded = False


def _ensure_params_loaded(conn: sqlite3.Connection) -> None:
    """Load FSRS parameters from config table on first connection."""
    import json
    import sqlite3 as _sqlite3

    global _params_loaded
    if _params_loaded:
        return
    _params_loaded = True
    try:
        params_json = db.get_config(conn, "fsrs_parameters")
    except _sqlite3.OperationalError:
        return  # config table doesn't exist yet (fresh DB before init)
    if params_json is not None:
        params: list[float] = json.loads(params_json)  # type: ignore[assignment]  # json.loads returns Any
        fsrs_engine.update_scheduler(params)


def reset_params_loaded() -> None:
    """Reset param loading flag and restore default scheduler. For tests."""
    global _params_loaded
    _params_loaded = False
    fsrs_engine.update_scheduler(list(fsrs_engine.DEFAULT_PARAMS))


@contextmanager
def _open_db(db_path: Path, *, require_exists: bool = True) -> Iterator[sqlite3.Connection]:
    """Open a database connection as a context manager."""
    if require_exists and not db_path.exists():
        raise DatabaseNotFoundError(db_path)
    conn = db.get_connection(db_path)
    try:
        db.migrate_db(conn)
        _ensure_params_loaded(conn)
        yield conn
    finally:
        conn.close()


def _validate_state(state: str | None) -> None:
    """Raise InvalidStateError if state is not a valid FSRS state name."""
    if state is not None and state not in db.VALID_STATES:
        raise InvalidStateError(state)


def init_database(db_path: Path) -> dict[str, str | int]:
    """Initialize the database. Idempotent."""
    with _open_db(db_path, require_exists=False) as conn:
        tables = db.init_db(conn)
        conn.commit()
        return {"status": "ok", "tables_created": tables, "db": str(db_path)}


def get_next_card(
    db_path: Path,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    search: str | None = None,
    due_before: str | None = None,
) -> CardDue | None:
    """Get the next due card for review, with optional filters."""
    _validate_state(state)
    with _open_db(db_path) as conn:
        return db.get_next_due_card(
            conn, deck=deck, tags=tags, state=state, search=search, due_before=due_before
        )


def get_next_due_time(db_path: Path) -> str | None:
    """Get the next due time when no cards are currently due."""
    with _open_db(db_path) as conn:
        return db.get_next_due_time(conn)


LEECH_THRESHOLD = 8


def submit_review(db_path: Path, review: ReviewInput) -> ReviewResult:
    """Submit a review for a card."""
    if review.rating < 1 or review.rating > 4:
        raise InvalidRatingError(str(review.rating))

    with _open_db(db_path) as conn:
        fsrs_card = db.get_fsrs_card(conn, review.card_id)
        if fsrs_card is None:
            raise CardNotFoundError(review.card_id)

        suspended_row = conn.execute(
            "SELECT suspended FROM cards WHERE id = ?", (review.card_id,)
        ).fetchone()
        if suspended_row and suspended_row["suspended"]:
            raise CardSuspendedError(review.card_id)

        # Leech detection: increment lapse count on "again" for Review/Relearning cards
        from fsrs import State

        is_leech = False
        if review.rating == 1 and fsrs_card.state in (State.Review, State.Relearning):
            lapse_count = db.increment_lapse_count(conn, review.card_id)
            if lapse_count >= LEECH_THRESHOLD:
                db.suspend_card(conn, review.card_id)
                is_leech = True

        updated_card, review_log = fsrs_engine.review_card(fsrs_card, review.rating)
        retrievability = fsrs_engine.get_retrievability(updated_card)

        db.update_fsrs_state(conn, review.card_id, updated_card, retrievability)
        db.insert_review_log(conn, review, review_log.to_json())
        conn.commit()

        interval_days = 0.0
        if updated_card.due and updated_card.last_review:
            delta = updated_card.due - updated_card.last_review
            interval_days = round(delta.total_seconds() / 86400, 2)

        last_review_str: str | None = None
        if updated_card.last_review is not None:
            last_review_str = updated_card.last_review.strftime("%Y-%m-%d %H:%M:%S")

        return ReviewResult(
            card_id=review.card_id,
            rating=fsrs_engine.rating_name(review.rating),
            new_state=fsrs_engine.state_name(updated_card.state, last_review_str),
            new_due=updated_card.due.strftime("%Y-%m-%d %H:%M:%S") if updated_card.due else "",
            stability=round(updated_card.stability or 0.0, 4),
            difficulty=round(updated_card.difficulty or 0.0, 4),
            interval_days=interval_days,
            is_leech=is_leech,
        )


def _basic_note_id(question: str, deck: str) -> int:
    """Generate a deterministic source_note_id for basic cards."""
    return int(hashlib.sha256(f"{question}\x1f{deck}".encode()).hexdigest()[:15], 16)


def add_card(
    db_path: Path,
    question: str,
    answer: str,
    deck: str = "Default",
    tags: str = "",
    source: CardSource = "manual",
) -> dict[str, int | str | bool]:
    """Add a new card. Re-adding the same question to the same deck updates the existing card."""
    if not question.strip():
        raise EmptyFieldError("question")
    if not answer.strip():
        raise EmptyFieldError("answer")
    with _open_db(db_path) as conn:
        deck_id = db.upsert_deck(conn, deck)
        note_id = _basic_note_id(question, deck)
        card = CardRecord(
            deck_id=deck_id,
            question=question,
            answer=answer,
            tags=tags,
            source=source,
            source_note_id=note_id,
            source_card_ord=0,
        )
        card_id, was_update = db.insert_card(conn, card)
        conn.commit()
        return {"card_id": card_id, "deck": deck, "was_update": was_update}


def add_cards_bulk(db_path: Path, cards: list[BulkCardInput]) -> BulkAddResult:
    """Add multiple cards in a single transaction."""
    with _open_db(db_path) as conn:
        created: list[int] = []
        for card_input in cards:
            deck_id = db.upsert_deck(conn, card_input.deck)
            if card_input.type == "cloze":
                note_id = _cloze_note_id(card_input.question)
                card_ids = _expand_cloze(
                    conn, card_input.question, deck_id, card_input.tags, note_id
                )
                created.extend(card_ids)
            else:
                note_id = _basic_note_id(card_input.question, card_input.deck)
                card = CardRecord(
                    deck_id=deck_id,
                    question=card_input.question,
                    answer=card_input.answer,
                    tags=card_input.tags,
                    source="manual",
                    source_note_id=note_id,
                    source_card_ord=0,
                )
                card_id, _ = db.insert_card(conn, card)
                created.append(card_id)
        conn.commit()
        return BulkAddResult(created=created, count=len(created))


_CLOZE_PATTERN = re.compile(r"\{\{c(\d+)::(.+?)(?:::.*?)?\}\}", re.DOTALL)


def _is_cloze_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Check if a card is part of a cloze note by looking for _cloze_source in extra_fields."""
    import json as _json

    row = conn.execute("SELECT extra_fields FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None or row["extra_fields"] is None:
        return False
    extra = _json.loads(row["extra_fields"])
    return "_cloze_source" in extra


def _cloze_note_id(text: str) -> int:
    """Generate a deterministic source_note_id from cloze text."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:15], 16)


def _expand_cloze(
    conn: sqlite3.Connection,
    text: str,
    deck_id: int,
    tags: str,
    source_note_id: int,
) -> list[int]:
    """Expand cloze text into N cards. Returns list of card IDs."""
    from spacedrep.anki_render import render_cloze

    cloze_nums = sorted({int(m[0]) for m in _CLOZE_PATTERN.findall(text) if int(m[0]) >= 1})
    if not cloze_nums:
        raise NoClozeMarkersError()

    card_ids: list[int] = []
    ordinals: set[int] = set()
    for num in cloze_nums:
        card_ord = num - 1
        ordinals.add(card_ord)
        question, answer = render_cloze(text, card_ord)

        # Preserve suspended status for existing cards (e.g. leech auto-suspend)
        existing_row = conn.execute(
            "SELECT suspended FROM cards WHERE source_note_id = ? AND source_card_ord = ?",
            (source_note_id, card_ord),
        ).fetchone()
        is_suspended = bool(existing_row["suspended"]) if existing_row else False

        card = CardRecord(
            deck_id=deck_id,
            question=question,
            answer=answer,
            extra_fields={"_cloze_source": text},
            tags=tags,
            source="generated",
            source_note_id=source_note_id,
            source_card_ord=card_ord,
            suspended=is_suspended,
        )
        card_id, _ = db.insert_card(conn, card)
        card_ids.append(card_id)

    # Orphan cleanup: delete cards from this note with ordinals not in the new set
    existing = conn.execute(
        "SELECT id, source_card_ord FROM cards WHERE source_note_id = ?",
        (source_note_id,),
    ).fetchall()
    for row in existing:
        if row["source_card_ord"] not in ordinals:
            db.delete_card(conn, row["id"])

    return card_ids


def add_cloze_note(
    db_path: Path,
    text: str,
    deck: str = "Default",
    tags: str = "",
) -> ClozeAddResult:
    """Add a cloze deletion note that expands into multiple flashcards."""
    if not _CLOZE_PATTERN.search(text):
        raise NoClozeMarkersError()

    note_id = _cloze_note_id(text)
    with _open_db(db_path) as conn:
        deck_id = db.upsert_deck(conn, deck)
        card_ids = _expand_cloze(conn, text, deck_id, tags, note_id)
        conn.commit()
        return ClozeAddResult(
            note_id=note_id, card_ids=card_ids, card_count=len(card_ids), deck=deck
        )


def update_cloze_note(
    db_path: Path,
    card_id: int,
    text: str,
    tags: str | None = None,
) -> ClozeAddResult:
    """Update a cloze note by providing any card ID from the note."""
    if not _CLOZE_PATTERN.search(text):
        raise NoClozeMarkersError()

    with _open_db(db_path) as conn:
        row = conn.execute(
            "SELECT source_note_id, tags, deck_id FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            raise CardNotFoundError(card_id)
        if row["source_note_id"] is None or not _is_cloze_card(conn, card_id):
            raise NotAClozeNoteError(card_id)

        source_note_id: int = row["source_note_id"]
        effective_tags = tags if tags is not None else row["tags"]
        deck_id: int = row["deck_id"]

        # Resolve deck name for the result
        deck_row = conn.execute("SELECT name FROM decks WHERE id = ?", (deck_id,)).fetchone()
        deck_name: str = deck_row["name"] if deck_row else "Default"

        card_ids = _expand_cloze(conn, text, deck_id, effective_tags, source_note_id)
        conn.commit()
        return ClozeAddResult(
            note_id=source_note_id,
            card_ids=card_ids,
            card_count=len(card_ids),
            deck=deck_name,
        )


def list_cards(
    db_path: Path,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leeches_only: bool = False,
    search: str | None = None,
    suspended: bool | None = None,
    source: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    created_before: str | None = None,
    created_after: str | None = None,
    reviewed_before: str | None = None,
    reviewed_after: str | None = None,
    min_difficulty: float | None = None,
    max_difficulty: float | None = None,
    min_stability: float | None = None,
    max_stability: float | None = None,
    min_retrievability: float | None = None,
    max_retrievability: float | None = None,
    buried: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CardListResult:
    """List cards with optional filters, paginated."""
    _validate_state(state)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    leech_threshold = LEECH_THRESHOLD if leeches_only else None
    with _open_db(db_path) as conn:
        return db.list_cards(
            conn,
            deck=deck,
            tags=tags,
            state=state,
            leech_threshold=leech_threshold,
            search=search,
            suspended=suspended,
            source=source,
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


def get_card_detail(db_path: Path, card_id: int) -> CardDetail:
    """Get full card detail. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        detail = db.get_card_detail(conn, card_id)
        if detail is None:
            raise CardNotFoundError(card_id)
        return detail


def get_review_history(db_path: Path, card_id: int) -> ReviewHistory:
    """Get review history for a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        detail = db.get_card_detail(conn, card_id)
        if detail is None:
            raise CardNotFoundError(card_id)
        reviews = db.get_review_history(conn, card_id)
        return ReviewHistory(card_id=card_id, reviews=reviews, total=len(reviews))


def preview_review(db_path: Path, card_id: int) -> ReviewPreview:
    """Preview what each of the 4 ratings would produce for a card."""
    from spacedrep.models import RatingPreview

    with _open_db(db_path) as conn:
        fsrs_card = db.get_fsrs_card(conn, card_id)
        if fsrs_card is None:
            raise CardNotFoundError(card_id)

        # Get current state info for the response
        row = conn.execute(
            "SELECT state, last_review FROM fsrs_state WHERE card_id = ?", (card_id,)
        ).fetchone()
        from fsrs import State

        current_state = fsrs_engine.state_name(State(row["state"]), row["last_review"])

        previews: dict[str, RatingPreview] = {}
        for rating_int, updated, _log in fsrs_engine.preview_card(fsrs_card):
            last_review_str: str | None = None
            if updated.last_review is not None:
                last_review_str = updated.last_review.strftime("%Y-%m-%d %H:%M:%S")

            interval_days = 0.0
            if updated.due and updated.last_review:
                delta = updated.due - updated.last_review
                interval_days = round(delta.total_seconds() / 86400, 2)

            name = fsrs_engine.rating_name(rating_int)
            previews[name] = RatingPreview(
                rating=name,
                new_state=fsrs_engine.state_name(updated.state, last_review_str),
                new_due=updated.due.strftime("%Y-%m-%d %H:%M:%S") if updated.due else "",
                stability=round(updated.stability or 0.0, 4),
                difficulty=round(updated.difficulty or 0.0, 4),
                interval_days=interval_days,
            )

        return ReviewPreview(
            card_id=card_id,
            current_state=current_state,
            previews=previews,
        )


def delete_card(
    db_path: Path, card_id: int, *, dry_run: bool = False
) -> dict[str, int | bool | str]:
    """Delete a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        if dry_run:
            detail = db.get_card_detail(conn, card_id)
            if detail is None:
                raise CardNotFoundError(card_id)
            return {
                "card_id": card_id,
                "action": "delete",
                "dry_run": True,
                "question": detail.question,
                "deck": detail.deck,
                "review_count": detail.review_count,
            }
        result = db.delete_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return {"card_id": card_id, "deleted": True}


def update_card(
    db_path: Path,
    card_id: int,
    *,
    question: str | None = None,
    answer: str | None = None,
    tags: str | None = None,
    deck: str | None = None,
) -> CardDetail:
    """Update card fields. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        deck_id: int | None = None
        if deck is not None:
            deck_id = db.upsert_deck(conn, deck)

        result = db.update_card(
            conn, card_id, question=question, answer=answer, tags=tags, deck_id=deck_id
        )
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()

        detail = db.get_card_detail(conn, card_id)
        if detail is None:
            raise CardNotFoundError(card_id)
        return detail


def suspend_card(db_path: Path, card_id: int, *, dry_run: bool = False) -> dict[str, int | bool]:
    """Suspend a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        if dry_run:
            detail = db.get_card_detail(conn, card_id)
            if detail is None:
                raise CardNotFoundError(card_id)
            return {
                "card_id": card_id,
                "suspended": True,
                "dry_run": True,
                "current_suspended": detail.suspended,
            }
        result = db.suspend_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return {"card_id": card_id, "suspended": True}


def unsuspend_card(db_path: Path, card_id: int, *, dry_run: bool = False) -> dict[str, int | bool]:
    """Unsuspend a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        if dry_run:
            detail = db.get_card_detail(conn, card_id)
            if detail is None:
                raise CardNotFoundError(card_id)
            return {
                "card_id": card_id,
                "suspended": False,
                "dry_run": True,
                "current_suspended": detail.suspended,
            }
        result = db.unsuspend_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return {"card_id": card_id, "suspended": False}


def bury_card(db_path: Path, card_id: int, hours: int = 24) -> dict[str, int | str]:
    """Bury a card for a number of hours. Raises CardNotFoundError if not found."""
    if hours < 1:
        raise InvalidBuryDurationError(hours)
    from datetime import UTC, datetime, timedelta

    until = (datetime.now(UTC) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with _open_db(db_path) as conn:
        result = db.bury_card(conn, card_id, until)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return {"card_id": card_id, "buried_until": until}


def unbury_card(db_path: Path, card_id: int) -> dict[str, int | bool]:
    """Unbury a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        result = db.unbury_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return {"card_id": card_id, "buried": False}


def import_deck(
    db_path: Path,
    apkg_path: Path,
    question_field: str | None = None,
    answer_field: str | None = None,
    *,
    dry_run: bool = False,
) -> ImportResult:
    """Import an .apkg file into the database."""
    from spacedrep.apkg_reader import read_apkg

    if not apkg_path.exists():
        raise ApkgImportError(f"File not found: {apkg_path}")
    if apkg_path.suffix.lower() != ".apkg":
        raise ApkgImportError(
            f"Expected .apkg file, got '{apkg_path.suffix or 'no extension'}': {apkg_path.name}"
        )

    try:
        decks, cards, field_info, note_deck_map = read_apkg(apkg_path, question_field, answer_field)
    except Exception as e:
        raise ApkgImportError(f"Failed to read .apkg: {e}") from e

    fields_val = field_info.get("fields", [])
    fields_list: list[str] = fields_val if isinstance(fields_val, list) else []
    q_val = field_info.get("question_field", "")
    q_str: str = q_val if isinstance(q_val, str) else ""
    a_val = field_info.get("answer_field", "")
    a_str: str = a_val if isinstance(a_val, str) else ""
    deck_names = [d.name for d in decks]
    first_deck = decks[0].name if decks else "Unknown"

    if dry_run:
        # Count how many would be new vs updated without writing
        with _open_db(db_path) as conn:
            would_import = 0
            would_update = 0
            for card in cards:
                if card.source_note_id is not None:
                    existing = conn.execute(
                        "SELECT 1 FROM cards WHERE source_note_id = ? AND source_card_ord = ?",
                        (card.source_note_id, card.source_card_ord),
                    ).fetchone()
                    if existing:
                        would_update += 1
                    else:
                        would_import += 1
                else:
                    would_import += 1
            return ImportResult(
                imported=would_import,
                updated=would_update,
                decks=deck_names if deck_names else [first_deck],
                fields=fields_list,
                question_field=q_str,
                answer_field=a_str,
                dry_run=True,
            )

    with _open_db(db_path) as conn:
        imported = 0
        updated = 0

        for deck_rec in decks:
            db.upsert_deck(conn, deck_rec.name, deck_rec.source_id)

        for card in cards:
            # Resolve per-card deck from the (note_id, ord)→deck mapping
            note_id = card.source_note_id
            if note_id is not None and (note_id, card.source_card_ord) in note_deck_map:
                card_deck = note_deck_map[(note_id, card.source_card_ord)]
            else:
                card_deck = first_deck
            deck_id = db.upsert_deck(conn, card_deck)
            card.deck_id = deck_id

            _, was_update = db.insert_card(conn, card)
            if was_update:
                updated += 1
            else:
                imported += 1

        conn.commit()
        return ImportResult(
            imported=imported,
            updated=updated,
            decks=deck_names if deck_names else [first_deck],
            fields=fields_list,
            question_field=q_str,
            answer_field=a_str,
        )


def export_deck(
    db_path: Path,
    output_path: Path,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    search: str | None = None,
    suspended: bool | None = None,
    source: str | None = None,
    buried: bool | None = None,
) -> int:
    """Export cards to an .apkg file. Returns count of exported cards.

    Filters by deck, tags, state, search, suspended, source, or buried.
    Non-existent deck returns 0 exported.
    """
    from spacedrep.apkg_writer import write_apkg

    _validate_state(state)
    with _open_db(db_path) as conn:
        cards = db.get_filtered_cards(
            conn,
            deck=deck,
            tags=tags,
            state=state,
            search=search,
            suspended=suspended,
            source=source,
            buried=buried,
        )
        deck_recs = db.get_deck_records(conn)
        return write_apkg(cards, deck_recs, output_path)


def list_decks(db_path: Path) -> list[DeckInfo]:
    """List all decks with card and due counts."""
    with _open_db(db_path) as conn:
        return db.list_decks(conn)


def list_tags(db_path: Path) -> list[str]:
    """Return all unique tags from the database, sorted alphabetically."""
    with _open_db(db_path) as conn:
        return db.list_tags(conn)


def get_due_count(db_path: Path) -> DueCount:
    """Get count of due cards by state."""
    with _open_db(db_path) as conn:
        return db.get_due_count(conn)


def get_session_stats(db_path: Path, session_id: str) -> SessionStats:
    """Get stats for a review session."""
    with _open_db(db_path) as conn:
        return db.get_session_stats(conn, session_id)


def get_overall_stats(db_path: Path) -> OverallStats:
    """Get overall database statistics."""
    with _open_db(db_path) as conn:
        return db.get_overall_stats(conn)


def _reschedule_all_cards(conn: sqlite3.Connection) -> int:
    """Reschedule all cards with current scheduler params. Returns count."""
    from fsrs import Card as FsrsCard
    from fsrs import ReviewLog as FsrsReviewLog

    scheduler = fsrs_engine.get_scheduler()
    card_ids = conn.execute("SELECT DISTINCT card_id FROM review_logs").fetchall()
    count = 0

    for row in card_ids:
        card_id: int = row["card_id"]
        log_jsons = db.get_review_logs_for_card(conn, card_id)
        if not log_jsons:
            continue

        review_logs = [FsrsReviewLog.from_json(j) for j in log_jsons]
        fresh_card = FsrsCard(card_id=review_logs[0].card_id)
        rescheduled = scheduler.reschedule_card(fresh_card, review_logs)
        retrievability = fsrs_engine.get_retrievability(rescheduled)
        db.update_fsrs_state(conn, card_id, rescheduled, retrievability)
        count += 1

    return count


def optimize_parameters(
    db_path: Path, *, reschedule: bool = False, dry_run: bool = False
) -> OptimizeResult:
    """Optimize FSRS parameters from review history."""
    import json as _json

    try:
        from fsrs import Optimizer
    except ImportError:
        raise OptimizerNotInstalledError() from None

    from fsrs import ReviewLog as FsrsReviewLog

    with _open_db(db_path) as conn:
        raw_logs = db.get_all_review_log_jsons(conn)
        if not raw_logs:
            return OptimizeResult(
                optimized=False,
                parameters=list(fsrs_engine.get_current_parameters()),
                review_count=0,
                rescheduled=0,
                message="No review logs found",
            )

        review_logs = [FsrsReviewLog.from_json(json_str) for _, json_str in raw_logs]
        try:
            optimizer = Optimizer(review_logs)
        except ImportError:
            raise OptimizerNotInstalledError() from None
        params = optimizer.compute_optimal_parameters()

        is_default = tuple(params) == fsrs_engine.DEFAULT_PARAMS

        if dry_run:
            cards_with_reviews = len({cid for cid, _ in raw_logs})
            return OptimizeResult(
                optimized=not is_default,
                parameters=params,
                review_count=len(review_logs),
                rescheduled=cards_with_reviews if reschedule and not is_default else 0,
                message="Dry run: no changes applied",
            )

        if not is_default:
            db.set_config(conn, "fsrs_parameters", _json.dumps(params))
            fsrs_engine.update_scheduler(params)

        rescheduled = 0
        if reschedule and not is_default:
            rescheduled = _reschedule_all_cards(conn)

        conn.commit()
        return OptimizeResult(
            optimized=not is_default,
            parameters=params,
            review_count=len(review_logs),
            rescheduled=rescheduled,
            message="Parameters optimized"
            if not is_default
            else f"Need {512 - len(review_logs)} more reviews for optimization",
        )


def get_fsrs_status(db_path: Path) -> FsrsStatus:
    """Get current FSRS scheduler status."""
    with _open_db(db_path) as conn:
        review_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM review_logs WHERE fsrs_log_json IS NOT NULL"
        ).fetchone()["cnt"]
        return FsrsStatus(
            parameters=list(fsrs_engine.get_current_parameters()),
            is_default=fsrs_engine.is_default_parameters(),
            review_count=review_count,
            min_reviews_needed=512,
            can_optimize=review_count >= 512,
        )
