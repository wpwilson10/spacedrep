"""Business logic orchestration layer.

Both CLI and (future) MCP call these functions. No CLI or MCP dependencies.
"""

from pathlib import Path

from spacedrep import db, fsrs_engine
from spacedrep.models import (
    CardDue,
    CardRecord,
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
            suggestion="Check the card ID with 'spacedrep deck list'",
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
    def __init__(self, rating: int) -> None:
        super().__init__(
            error_code="invalid_rating",
            message=f"Invalid rating: {rating}. Must be 1-4 (again/hard/good/easy)",
            suggestion="Use: 1=again, 2=hard, 3=good, 4=easy",
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


def _require_db(db_path: Path) -> None:
    """Raise if the database file doesn't exist."""
    if not db_path.exists():
        raise DatabaseNotFoundError(db_path)


def init_database(db_path: Path) -> dict[str, str | int]:
    """Initialize the database. Idempotent."""
    conn = db.get_connection(db_path)
    try:
        tables = db.init_db(conn)
        conn.commit()
        return {"status": "ok", "tables_created": tables, "db": str(db_path)}
    finally:
        conn.close()


def get_next_card(db_path: Path) -> CardDue | None:
    """Get the next due card for review."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.get_next_due_card(conn)
    finally:
        conn.close()


def get_next_due_time(db_path: Path) -> str | None:
    """Get the next due time when no cards are currently due."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.get_next_due_time(conn)
    finally:
        conn.close()


def submit_review(db_path: Path, review: ReviewInput) -> ReviewResult:
    """Submit a review for a card."""
    _require_db(db_path)
    if review.rating < 1 or review.rating > 4:
        raise InvalidRatingError(review.rating)

    conn = db.get_connection(db_path)
    try:
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

        return ReviewResult(
            card_id=review.card_id,
            rating=fsrs_engine.rating_name(review.rating),
            new_state=fsrs_engine.state_name(updated_card.state),
            new_due=updated_card.due,
            stability=round(updated_card.stability or 0.0, 4),
            difficulty=round(updated_card.difficulty or 0.0, 4),
            interval_days=interval_days,
        )
    finally:
        conn.close()


def add_card(
    db_path: Path,
    question: str,
    answer: str,
    deck: str = "Default",
    tags: str = "",
    source: str = "manual",
) -> dict[str, int | str]:
    """Add a new card."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        deck_id = db.upsert_deck(conn, deck)
        card = CardRecord(
            deck_id=deck_id,
            question=question,
            answer=answer,
            tags=tags,
            source=source,  # type: ignore[arg-type]
        )
        card_id = db.insert_card(conn, card)
        conn.commit()
        return {"card_id": card_id, "deck": deck}
    finally:
        conn.close()


def suspend_card(db_path: Path, card_id: int) -> bool:
    """Suspend a card. Returns False if not found."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        result = db.suspend_card(conn, card_id)
        conn.commit()
        if not result:
            raise CardNotFoundError(card_id)
        return result
    finally:
        conn.close()


def unsuspend_card(db_path: Path, card_id: int) -> bool:
    """Unsuspend a card. Returns False if not found."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        result = db.unsuspend_card(conn, card_id)
        conn.commit()
        if not result:
            raise CardNotFoundError(card_id)
        return result
    finally:
        conn.close()


def import_deck(
    db_path: Path,
    apkg_path: Path,
    question_field: str | None = None,
    answer_field: str | None = None,
) -> ImportResult:
    """Import an .apkg file into the database."""
    from spacedrep.apkg_reader import read_apkg

    _require_db(db_path)
    if not apkg_path.exists():
        raise ApkgImportError(f"File not found: {apkg_path}")

    try:
        decks, cards, field_info = read_apkg(apkg_path, question_field, answer_field)
    except Exception as e:
        raise ApkgImportError(f"Failed to read .apkg: {e}") from e

    conn = db.get_connection(db_path)
    try:
        imported = 0
        updated = 0
        skipped = 0
        deck_name = decks[0].name if decks else "Unknown"

        for deck_rec in decks:
            db.upsert_deck(conn, deck_rec.name, deck_rec.source_id)

        for card in cards:
            deck_id = db.upsert_deck(conn, deck_name)
            card.deck_id = deck_id

            if card.source_note_id is not None:
                existing = conn.execute(
                    "SELECT id FROM cards WHERE source_note_id = ?",
                    (card.source_note_id,),
                ).fetchone()
                if existing:
                    db.insert_card(conn, card)  # updates via dedup logic
                    updated += 1
                else:
                    db.insert_card(conn, card)
                    imported += 1
            else:
                db.insert_card(conn, card)
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
            skipped=skipped,
            deck=deck_name,
            fields=fields_list,
            question_field=q_str,
            answer_field=a_str,
        )
    finally:
        conn.close()


def export_deck(db_path: Path, output_path: Path, deck: str | None = None) -> int:
    """Export cards to an .apkg file. Returns count of exported cards."""
    from spacedrep.apkg_writer import write_apkg
    from spacedrep.models import DeckRecord

    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        deck_id: int | None = None
        if deck:
            row = conn.execute("SELECT id FROM decks WHERE name = ?", (deck,)).fetchone()
            if row:
                deck_id = row["id"]

        cards = db.get_all_cards(conn, deck_id)
        deck_records = db.list_decks(conn)
        deck_recs = [DeckRecord(name=d.name) for d in deck_records]
        return write_apkg(cards, deck_recs, output_path)
    finally:
        conn.close()


def list_decks(db_path: Path) -> list[DeckInfo]:
    """List all decks with card and due counts."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.list_decks(conn)
    finally:
        conn.close()


def get_due_count(db_path: Path) -> DueCount:
    """Get count of due cards by state."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.get_due_count(conn)
    finally:
        conn.close()


def get_session_stats(db_path: Path, session_id: str) -> SessionStats:
    """Get stats for a review session."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.get_session_stats(conn, session_id)
    finally:
        conn.close()


def get_overall_stats(db_path: Path) -> OverallStats:
    """Get overall database statistics."""
    _require_db(db_path)
    conn = db.get_connection(db_path)
    try:
        return db.get_overall_stats(conn)
    finally:
        conn.close()
