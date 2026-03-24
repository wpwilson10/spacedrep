"""Business logic orchestration layer.

Both CLI and (future) MCP call these functions. No CLI or MCP dependencies.
"""

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
    DeckInfo,
    DueCount,
    FsrsStatus,
    ImportResult,
    OptimizeResult,
    OverallStats,
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


class BulkInputError(SpacedrepError):
    def __init__(self, message: str) -> None:
        super().__init__(
            error_code="bulk_input_error",
            message=message,
            suggestion="Input must be a JSON array of {question, answer, deck?, tags?}",
            exit_code=2,
        )


class OptimizerNotInstalledError(SpacedrepError):
    def __init__(self) -> None:
        super().__init__(
            error_code="optimizer_not_installed",
            message="FSRS optimizer requires the 'optimizer' extra",
            suggestion="Install with: pip install spacedrep[optimizer]",
            exit_code=1,
        )


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
) -> CardDue | None:
    """Get the next due card for review, with optional filters."""
    _validate_state(state)
    with _open_db(db_path) as conn:
        return db.get_next_due_card(conn, deck=deck, tags=tags, state=state)


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


def add_cards_bulk(db_path: Path, cards: list[BulkCardInput]) -> BulkAddResult:
    """Add multiple cards in a single transaction."""
    with _open_db(db_path) as conn:
        created: list[int] = []
        for card_input in cards:
            deck_id = db.upsert_deck(conn, card_input.deck)
            card = CardRecord(
                deck_id=deck_id,
                question=card_input.question,
                answer=card_input.answer,
                tags=card_input.tags,
                source="manual",
            )
            card_id, _ = db.insert_card(conn, card)
            created.append(card_id)
        conn.commit()
        return BulkAddResult(created=created, count=len(created))


def list_cards(
    db_path: Path,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leeches_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> CardListResult:
    """List cards with optional filters, paginated."""
    _validate_state(state)
    leech_threshold = LEECH_THRESHOLD if leeches_only else None
    with _open_db(db_path) as conn:
        return db.list_cards(
            conn,
            deck=deck,
            tags=tags,
            state=state,
            leech_threshold=leech_threshold,
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


def optimize_parameters(db_path: Path, *, reschedule: bool = False) -> OptimizeResult:
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
