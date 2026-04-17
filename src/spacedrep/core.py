"""Business logic orchestration layer.

Both CLI and (future) MCP call these functions. No CLI or MCP dependencies.
"""

import json as _json
import re
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spacedrep import db, fsrs_engine
from spacedrep.anki_schema import (
    ANKI_SCHEMA,
    CLOZE_MODEL_ID,
    ColMeta,
    basic_guid,
    cloze_guid,
    reversed_guid,
)
from spacedrep.models import (
    BulkAddResult,
    BulkCardInput,
    CardDetail,
    CardDue,
    CardListResult,
    CardSource,
    ClozeAddResult,
    DeckInfo,
    DueCount,
    FsrsStatus,
    OpenResult,
    OptimizeResult,
    OverallStats,
    ReversedAddResult,
    ReviewHistory,
    ReviewInput,
    ReviewPreview,
    ReviewResult,
    SaveResult,
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


class UpdateClozeCardError(SpacedrepError):
    def __init__(self, card_id: int) -> None:
        super().__init__(
            error_code="update_cloze_card",
            message=f"Card {card_id} is a cloze card; update_card is not supported for cloze.",
            suggestion="Use update_cloze_note(card_id=..., text=...) instead.",
            exit_code=2,
            card_id=card_id,
        )


class UnsupportedCollectionFormatError(SpacedrepError):
    def __init__(self, db_path: Path) -> None:
        super().__init__(
            error_code="unsupported_collection_format",
            message=(
                f"{db_path} uses the Anki 2.1.49+ split-table schema "
                "(notetypes/templates/fields). spacedrep currently reads "
                "the legacy col.models JSON layout only."
            ),
            suggestion=(
                "Re-export from Anki Desktop as an .apkg with 'Support "
                "older Anki versions' checked (File \u2192 Export), then "
                "`spacedrep deck import` it; or point SPACEDREP_DB / --db "
                "at a fresh file initialized with `spacedrep db init`."
            ),
            exit_code=2,
            db_path=str(db_path),
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


class ExportError(SpacedrepError):
    def __init__(self, message: str, suggestion: str = "") -> None:
        super().__init__(
            error_code="export_error",
            message=message,
            suggestion=suggestion or "Check the output path and try again",
            exit_code=2,
        )


class InvalidDateError(SpacedrepError):
    def __init__(self, field: str, value: str) -> None:
        super().__init__(
            error_code="invalid_date",
            message=f"Invalid date for '{field}': {value!r}",
            suggestion="Use ISO 8601 format, e.g. '2025-01-15' or '2025-01-15T10:30:00'",
            exit_code=2,
            field=field,
            value=value,
        )


# Concurrency note: _params_loaded guards a one-time load of FSRS parameters
# from the database into the module-level scheduler. This is safe because
# FastMCP serializes tool calls via asyncio (single-threaded). If concurrency
# model changes, this must become a lock-protected initialization.
_params_loaded = False


def _ensure_params_loaded(conn: sqlite3.Connection) -> None:
    """Load FSRS parameters from config table on first connection."""
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
        params: list[float] = _json.loads(params_json)  # type: ignore[assignment]  # json.loads returns Any
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
        if db.is_modern_anki_schema(conn):
            raise UnsupportedCollectionFormatError(db_path)
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
            "SELECT queue FROM cards WHERE id = ?", (review.card_id,)
        ).fetchone()
        if suspended_row and suspended_row["queue"] == -1:
            raise CardSuspendedError(review.card_id)

        # Leech detection: increment lapse count on "again" for Review/Relearning cards
        from fsrs import State

        is_leech = False
        if review.rating == 1 and fsrs_card.state in (State.Review, State.Relearning):
            lapse_count = db.increment_lapse_count(conn, review.card_id)
            if lapse_count >= LEECH_THRESHOLD:
                is_leech = True

        updated_card, review_log = fsrs_engine.review_card(fsrs_card, review.rating)
        retrievability = fsrs_engine.get_retrievability(updated_card)

        db.update_fsrs_state(conn, review.card_id, updated_card, retrievability)
        db.insert_review_log(
            conn,
            review,
            review_log.to_json(),
            updated_card=updated_card,
            previous_card=fsrs_card,
        )

        # Suspend after update_fsrs_state so queue=-1 isn't overwritten
        if is_leech:
            db.suspend_card(conn, review.card_id)

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
    if not deck.strip():
        raise EmptyFieldError("deck")
    with _open_db(db_path) as conn:
        guid = basic_guid(question, deck)
        card_id, was_update = db.insert_card(
            conn,
            question=question,
            answer=answer,
            deck_name=deck,
            tags=tags,
            source=source,
            guid=guid,
        )
        conn.commit()
        return {"card_id": card_id, "deck": deck, "was_update": was_update}


def add_cards_bulk(db_path: Path, cards: list[BulkCardInput]) -> BulkAddResult:
    """Add multiple cards in a single transaction."""
    with _open_db(db_path) as conn:
        created: list[int] = []
        for card_input in cards:
            if card_input.type == "cloze":
                guid = cloze_guid(card_input.question)
                card_ids = _expand_cloze(
                    conn, card_input.question, card_input.deck, card_input.tags, guid
                )
                created.extend(card_ids)
            elif card_input.type == "reversed":
                rev_guid = reversed_guid(card_input.question, card_input.deck)
                _, rev_card_ids, _ = db.insert_reversed_note(
                    conn,
                    question=card_input.question,
                    answer=card_input.answer,
                    deck_name=card_input.deck,
                    tags=card_input.tags,
                    source="manual",
                    guid=rev_guid,
                )
                created.extend(rev_card_ids)
            else:
                guid = basic_guid(card_input.question, card_input.deck)
                card_id, _ = db.insert_card(
                    conn,
                    question=card_input.question,
                    answer=card_input.answer,
                    deck_name=card_input.deck,
                    tags=card_input.tags,
                    source="manual",
                    guid=guid,
                )
                created.append(card_id)
        conn.commit()
        # Preserve order, drop duplicate ids from re-adds (dedup hits).
        # Semantics: `created` is the set of card IDs that exist as a
        # result of this call; `count` is the size of that set.
        deduped: list[int] = list(dict.fromkeys(created))
        return BulkAddResult(created=deduped, count=len(deduped))


def add_reversed_card(
    db_path: Path,
    question: str,
    answer: str,
    deck: str = "Default",
    tags: str = "",
    source: CardSource = "manual",
) -> ReversedAddResult:
    """Add a reversed card pair: one note, two cards (Q→A and A→Q).

    Re-adding with the same (question, deck) updates the existing note in
    place — both cards reflect the new answer and review history is
    preserved.
    """
    if not question.strip():
        raise EmptyFieldError("question")
    if not answer.strip():
        raise EmptyFieldError("answer")
    if not deck.strip():
        raise EmptyFieldError("deck")
    with _open_db(db_path) as conn:
        guid = reversed_guid(question, deck)
        note_id, card_ids, _ = db.insert_reversed_note(
            conn,
            question=question,
            answer=answer,
            deck_name=deck,
            tags=tags,
            source=source,
            guid=guid,
        )
        conn.commit()
        return ReversedAddResult(
            note_id=note_id,
            card_ids=card_ids,
            card_count=len(card_ids),
            deck=deck,
        )


_CLOZE_PATTERN = re.compile(r"\{\{c(\d+)::(.+?)(?:::.*?)?\}\}", re.DOTALL)


def _expand_cloze(
    conn: sqlite3.Connection,
    text: str,
    deck_name: str,
    tags: str,
    guid: str,
) -> list[int]:
    """Expand cloze text into 1 note + N cards (Anki style). Returns list of card IDs."""
    cloze_nums = sorted({int(m[0]) for m in _CLOZE_PATTERN.findall(text) if int(m[0]) >= 1})
    if not cloze_nums:
        raise NoClozeMarkersError()

    now = int(time.time())
    meta = db.load_col_meta(conn)
    did = meta.ensure_deck(deck_name)
    mid = meta.ensure_model("cloze")
    db.save_col_meta(conn, meta)

    note_flds = f"{text}\x1f"
    new_ordinals = {num - 1 for num in cloze_nums}

    # Check for existing note (dedup on guid)
    existing = conn.execute("SELECT id FROM notes WHERE guid = ?", (guid,)).fetchone()
    if existing:
        note_id = int(existing["id"])
        conn.execute(
            "UPDATE notes SET flds=?, sfld=?, tags=?, mod=?, usn=-1 WHERE id=?",
            (note_flds, text, tags, now, note_id),
        )
        # Get existing card ordinals
        existing_cards = conn.execute(
            "SELECT id, ord FROM cards WHERE nid = ?", (note_id,)
        ).fetchall()
        existing_ords = {int(r["ord"]): int(r["id"]) for r in existing_cards}

        # Insert new cards first (before deleting orphans) to ensure the note
        # always has at least one card — prevents auto-orphan-cleanup in
        # delete_card from deleting the note when all ordinals change.
        card_ids: list[int] = []
        pos_row = conn.execute("SELECT MAX(due) AS maxdue FROM cards WHERE type = 0").fetchone()
        position = (
            (int(pos_row["maxdue"]) + 1) if (pos_row and pos_row["maxdue"] is not None) else 0
        )
        for ord_val in sorted(new_ordinals):
            if ord_val in existing_ords:
                card_ids.append(existing_ords[ord_val])
            else:
                card_id = db.next_id()
                conn.execute(
                    "INSERT INTO cards"
                    " (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
                    "  factor, reps, lapses, left, odue, odid, flags, data)"
                    " VALUES (?, ?, ?, ?, ?, -1, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
                    (card_id, note_id, did, ord_val, now, position),
                )
                position += 1
                card_ids.append(card_id)

        # Delete orphan cards (ordinals not in the new set)
        for ord_val, cid in existing_ords.items():
            if ord_val not in new_ordinals:
                db.delete_card(conn, cid)

        return card_ids

    # Insert new note
    note_id = db.next_id()
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, '')",
        (note_id, guid, mid, now, tags, note_flds, text),
    )

    # Insert cards with correct ordinals (cloze num - 1)
    card_ids = db.insert_cloze_cards(
        conn, note_id=note_id, did=did, ordinals=sorted(new_ordinals), now=now
    )

    db.invalidate_model_cache(conn)
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
    if not deck.strip():
        raise EmptyFieldError("deck")

    guid = cloze_guid(text)
    with _open_db(db_path) as conn:
        card_ids = _expand_cloze(conn, text, deck, tags, guid)
        conn.commit()
        # Use the note id from the first card
        note_row = conn.execute("SELECT nid FROM cards WHERE id = ?", (card_ids[0],)).fetchone()
        note_id = int(note_row["nid"]) if note_row else 0
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
        # Find the note via the card
        row = conn.execute(
            "SELECT c.nid, n.tags, n.mid, c.did FROM cards c JOIN notes n ON n.id = c.nid"
            " WHERE c.id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            raise CardNotFoundError(card_id)

        # Check if it's a cloze model
        if int(row["mid"]) != CLOZE_MODEL_ID:
            raise NotAClozeNoteError(card_id)

        note_id: int = int(row["nid"])
        effective_tags = tags if tags is not None else row["tags"]
        did: int = int(row["did"])

        # Resolve deck name for the result
        deck_name = db.deck_name_for_did(conn, did)

        # Get the guid of the existing note for dedup
        guid_row = conn.execute("SELECT guid FROM notes WHERE id = ?", (note_id,)).fetchone()
        guid: str = guid_row["guid"] if guid_row else cloze_guid(text)

        card_ids = _expand_cloze(conn, text, deck_name, effective_tags, guid)
        conn.commit()
        return ClozeAddResult(
            note_id=note_id,
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

        # Get current state from card columns
        row = conn.execute("SELECT type, data FROM cards WHERE id = ?", (card_id,)).fetchone()
        lrt = db.get_last_review_ts(row["data"]) if row else None
        current_state = db.card_state_name(row["type"], lrt)

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
            if not deck.strip():
                raise EmptyFieldError("deck")
            deck_id = db.upsert_deck(conn, deck)

        try:
            result = db.update_card(
                conn, card_id, question=question, answer=answer, tags=tags, deck_id=deck_id
            )
        except db.ClozeUpdateNotSupportedError as e:
            raise UpdateClozeCardError(e.card_id) from e
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
) -> OpenResult:
    """Import an .apkg file into the database.

    Delegates to open_deck(). The question_field, answer_field, and dry_run
    parameters are ignored — kept for API compatibility during transition.
    """
    return open_deck(db_path, apkg_path, force=True)


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

    Deprecated: use save_deck instead. This will be removed in a future version.
    """
    raise NotImplementedError(
        "export_deck is deprecated in the Anki-native schema. Use save_deck() instead."
    )


def open_deck(db_path: Path, apkg_path: Path, *, force: bool = False) -> OpenResult:
    """Extract an .apkg ZIP and copy the SQLite DB to db_path, adding extension tables.

    If db_path already exists with cards and force is False, raises an error.
    """
    if not apkg_path.exists():
        raise ApkgImportError(f"File not found: {apkg_path}")
    if apkg_path.suffix.lower() != ".apkg":
        raise ApkgImportError(
            f"Expected .apkg file, got '{apkg_path.suffix or 'no extension'}': {apkg_path.name}"
        )

    # Check if target db exists and has cards
    if db_path.exists() and not force:
        try:
            conn = db.get_connection(db_path)
            card_count = conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
            conn.close()
            if card_count and card_count["cnt"] > 0:
                raise ApkgImportError(
                    f"Database {db_path} already has {card_count['cnt']} cards. "
                    "Use the force option to overwrite."
                )
        except sqlite3.OperationalError:
            pass  # No cards table -- safe to overwrite

    # Extract the .apkg ZIP
    try:
        with zipfile.ZipFile(str(apkg_path), "r") as zf:
            names = zf.namelist()
            # Anki 2.1+ uses collection.anki21, older uses collection.anki2
            collection_name: str | None = None
            for candidate in ("collection.anki21", "collection.anki2"):
                if candidate in names:
                    collection_name = candidate
                    break
            if collection_name is None:
                raise ApkgImportError(
                    f"No collection database found in {apkg_path.name}. "
                    "Expected collection.anki21 or collection.anki2."
                )
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extract(collection_name, tmpdir)
                extracted = Path(tmpdir) / collection_name
                shutil.copy2(str(extracted), str(db_path))
    except zipfile.BadZipFile as e:
        raise ApkgImportError(f"Invalid .apkg file: {e}") from e

    # Add extension tables to the copied database
    conn = db.get_connection(db_path)
    try:
        conn.executescript(ANKI_SCHEMA)
        if db.is_modern_anki_schema(conn):
            raise UnsupportedCollectionFormatError(db_path)
        # Ensure col row exists (it should from the .apkg)
        col_exists = conn.execute("SELECT id FROM col WHERE id = 1").fetchone()
        if col_exists is None:
            meta = ColMeta.default()
            row = meta.to_col_row()
            conn.execute(
                "INSERT INTO col"
                " (id, crt, mod, scm, ver, dty, usn, ls,"
                " conf, models, decks, dconf, tags)"
                " VALUES (:id, :crt, :mod, :scm, :ver, :dty, :usn, :ls,"
                " :conf, :models, :decks, :dconf, :tags)",
                row,
            )
        # Count cards and decks
        card_count = conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
        deck_count_row = conn.execute("SELECT decks FROM col WHERE id = 1").fetchone()
        deck_count = 0
        deck_names: list[str] = []
        if deck_count_row:
            decks_json = _json.loads(deck_count_row["decks"])
            deck_count = len(decks_json)
            deck_names = [str(d.get("name", "Unknown")) for d in decks_json.values()]
        conn.commit()
    finally:
        conn.close()

    return OpenResult(
        db_path=str(db_path),
        card_count=int(card_count["cnt"]) if card_count else 0,
        deck_count=deck_count,
        decks=deck_names,
    )


def save_deck(db_path: Path, output_path: Path) -> SaveResult:
    """Copy the working SQLite DB and ZIP it as an .apkg.

    The .apkg contains collection.anki21 + a media file ({}).
    """
    if not db_path.exists():
        raise DatabaseNotFoundError(db_path)
    if not output_path.parent.exists():
        raise ExportError(
            message=f"Output directory does not exist: {output_path.parent}",
            suggestion="Create the parent directory first, or use an existing path",
        )

    # Count cards before packaging
    conn = db.get_connection(db_path)
    try:
        card_count = conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
        count = int(card_count["cnt"]) if card_count else 0
    finally:
        conn.close()

    # Create a clean copy without extension tables for Anki compatibility
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = Path(tmpdir) / "collection.anki21"
        shutil.copy2(str(db_path), str(tmp_db))

        # Strip spacedrep extension tables so Anki sees a clean schema
        tmp_conn = sqlite3.connect(str(tmp_db))
        tmp_conn.execute("DROP TABLE IF EXISTS spacedrep_meta")
        tmp_conn.execute("DROP TABLE IF EXISTS spacedrep_card_extra")
        tmp_conn.execute("DROP TABLE IF EXISTS spacedrep_review_extra")
        tmp_conn.commit()
        tmp_conn.execute("VACUUM")
        tmp_conn.close()

        # Write media file (empty JSON object)
        media_path = Path(tmpdir) / "media"
        media_path.write_text("{}")

        # Create ZIP
        with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(tmp_db), "collection.anki21")
            zf.write(str(media_path), "media")

    return SaveResult(
        output_path=str(output_path),
        card_count=count,
    )


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
    card_ids = conn.execute("SELECT DISTINCT cid AS card_id FROM revlog").fetchall()
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
                message="Dry run: parameters would be optimized"
                if not is_default
                else f"Dry run: need {512 - len(review_logs)} more reviews for optimization",
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
        review_count = conn.execute("SELECT COUNT(*) AS cnt FROM revlog").fetchone()["cnt"]
        return FsrsStatus(
            parameters=list(fsrs_engine.get_current_parameters()),
            is_default=fsrs_engine.is_default_parameters(),
            review_count=review_count,
            min_reviews_needed=512,
            can_optimize=review_count >= 512,
        )
