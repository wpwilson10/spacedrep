"""Business logic orchestration layer.

Both CLI and (future) MCP call these functions. No CLI or MCP dependencies.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spacedrep import db, fsrs_engine
from spacedrep.models import (
    CardDetail,
    CardDue,
    CardListResult,
    CardRecord,
    CardSource,
    DeckInfo,
    DueCount,
    ImportResult,
    OverallStats,
    ReviewInput,
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
            message="Provide at least one of --question, --answer, --tags, --deck",
            suggestion="Use --question, --answer, --tags, or --deck",
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


@contextmanager
def _open_db(db_path: Path, *, require_exists: bool = True) -> Iterator[sqlite3.Connection]:
    """Open a database connection as a context manager."""
    if require_exists and not db_path.exists():
        raise DatabaseNotFoundError(db_path)
    conn = db.get_connection(db_path)
    try:
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
) -> CardDue | None:
    """Get the next due card for review, with optional filters."""
    _validate_state(state)
    with _open_db(db_path) as conn:
        return db.get_next_due_card(conn, deck=deck, tags=tags, state=state)


def get_next_due_time(db_path: Path) -> str | None:
    """Get the next due time when no cards are currently due."""
    with _open_db(db_path) as conn:
        return db.get_next_due_time(conn)


def submit_review(db_path: Path, review: ReviewInput) -> ReviewResult:
    """Submit a review for a card."""
    if review.rating < 1 or review.rating > 4:
        raise InvalidRatingError(str(review.rating))

    with _open_db(db_path) as conn:
        fsrs_card = db.get_fsrs_card(conn, review.card_id)
        if fsrs_card is None:
            raise CardNotFoundError(review.card_id)

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
        )


def add_card(
    db_path: Path,
    question: str,
    answer: str,
    deck: str = "Default",
    tags: str = "",
    source: CardSource = "manual",
) -> dict[str, int | str]:
    """Add a new card."""
    with _open_db(db_path) as conn:
        deck_id = db.upsert_deck(conn, deck)
        card = CardRecord(
            deck_id=deck_id,
            question=question,
            answer=answer,
            tags=tags,
            source=source,
        )
        card_id, _ = db.insert_card(conn, card)
        conn.commit()
        return {"card_id": card_id, "deck": deck}


def list_cards(
    db_path: Path,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CardListResult:
    """List cards with optional filters, paginated."""
    _validate_state(state)
    with _open_db(db_path) as conn:
        return db.list_cards(conn, deck=deck, tags=tags, state=state, limit=limit, offset=offset)


def get_card_detail(db_path: Path, card_id: int) -> CardDetail:
    """Get full card detail. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
        detail = db.get_card_detail(conn, card_id)
        if detail is None:
            raise CardNotFoundError(card_id)
        return detail


def delete_card(db_path: Path, card_id: int) -> dict[str, int | bool]:
    """Delete a card. Raises CardNotFoundError if not found."""
    with _open_db(db_path) as conn:
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


def suspend_card(db_path: Path, card_id: int) -> bool:
    """Suspend a card. Returns False if not found."""
    with _open_db(db_path) as conn:
        result = db.suspend_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return result


def unsuspend_card(db_path: Path, card_id: int) -> bool:
    """Unsuspend a card. Returns False if not found."""
    with _open_db(db_path) as conn:
        result = db.unsuspend_card(conn, card_id)
        if not result:
            raise CardNotFoundError(card_id)
        conn.commit()
        return result


def import_deck(
    db_path: Path,
    apkg_path: Path,
    question_field: str | None = None,
    answer_field: str | None = None,
) -> ImportResult:
    """Import an .apkg file into the database."""
    from spacedrep.apkg_reader import read_apkg

    if not apkg_path.exists():
        raise ApkgImportError(f"File not found: {apkg_path}")

    try:
        decks, cards, field_info, note_deck_map = read_apkg(apkg_path, question_field, answer_field)
    except Exception as e:
        raise ApkgImportError(f"Failed to read .apkg: {e}") from e

    with _open_db(db_path) as conn:
        imported = 0
        updated = 0
        first_deck = decks[0].name if decks else "Unknown"

        for deck_rec in decks:
            db.upsert_deck(conn, deck_rec.name, deck_rec.source_id)

        for card in cards:
            # Resolve per-card deck from the note→deck mapping
            if card.source_note_id and card.source_note_id in note_deck_map:
                card_deck = note_deck_map[card.source_note_id]
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
        fields_val = field_info.get("fields", [])
        fields_list: list[str] = fields_val if isinstance(fields_val, list) else []
        q_val = field_info.get("question_field", "")
        q_str: str = q_val if isinstance(q_val, str) else ""
        a_val = field_info.get("answer_field", "")
        a_str: str = a_val if isinstance(a_val, str) else ""
        return ImportResult(
            imported=imported,
            updated=updated,
            deck=first_deck,
            fields=fields_list,
            question_field=q_str,
            answer_field=a_str,
        )


def export_deck(db_path: Path, output_path: Path, deck: str | None = None) -> int:
    """Export cards to an .apkg file. Returns count of exported cards."""
    from spacedrep.apkg_writer import write_apkg

    with _open_db(db_path) as conn:
        deck_id: int | None = None
        if deck:
            row = conn.execute("SELECT id FROM decks WHERE name = ?", (deck,)).fetchone()
            if row is None:
                raise SpacedrepError(
                    error_code="deck_not_found",
                    message=f"Deck '{deck}' not found",
                    suggestion="Check available decks with 'spacedrep deck list'",
                    exit_code=3,
                )
            deck_id = row["id"]

        cards = db.get_all_cards(conn, deck_id)
        deck_recs = db.get_deck_records(conn)
        return write_apkg(cards, deck_recs, output_path)


def list_decks(db_path: Path) -> list[DeckInfo]:
    """List all decks with card and due counts."""
    with _open_db(db_path) as conn:
        return db.list_decks(conn)


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
