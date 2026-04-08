"""SQLite schema and all database queries."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fsrs import Card, State

from spacedrep import fsrs_engine
from spacedrep.models import (
    CardDetail,
    CardDue,
    CardListResult,
    CardRecord,
    CardSummary,
    DeckInfo,
    DeckRecord,
    DueCount,
    OverallStats,
    ReviewInput,
    SessionStats,
)

_SQLITE_DT_FMT = "%Y-%m-%d %H:%M:%S"

VALID_STATES = frozenset({"new", "learning", "review", "relearning"})


def _to_sqlite_dt(dt: datetime) -> str:
    """Convert a datetime to SQLite-compatible format (UTC, no timezone)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime(_SQLITE_DT_FMT)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    extra_fields TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    source_note_id INTEGER,
    source_note_guid TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    suspended INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fsrs_state (
    card_id INTEGER PRIMARY KEY REFERENCES cards(id),
    fsrs_card_json TEXT NOT NULL,
    due TEXT NOT NULL,
    state INTEGER NOT NULL DEFAULT 1,
    stability REAL,
    difficulty REAL,
    last_review TEXT,
    retrievability REAL,
    lapse_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    rating INTEGER NOT NULL,
    user_answer TEXT,
    feedback TEXT,
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now')),
    session_id TEXT,
    fsrs_log_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_source_note
    ON cards(source_note_id) WHERE source_note_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fsrs_due ON fsrs_state(due);
CREATE INDEX IF NOT EXISTS idx_fsrs_state ON fsrs_state(state);
CREATE INDEX IF NOT EXISTS idx_review_card ON review_logs(card_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_review_session ON review_logs(session_id);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_TABLE_COUNT = 5


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> int:
    """Execute all CREATE TABLE/INDEX statements. Returns number of tables created."""
    conn.executescript(_SCHEMA)
    migrate_db(conn)
    return _TABLE_COUNT


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    """Add a column to a table if it doesn't already exist. No-op if table is missing."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return  # Table doesn't exist yet; schema creation will handle it
    columns = [r[1] for r in rows]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(conn: sqlite3.Connection) -> None:
    """Run idempotent schema migrations."""
    _add_column_if_missing(conn, "fsrs_state", "lapse_count", "INTEGER NOT NULL DEFAULT 0")
    # Convert comma-separated tags to space-separated (idempotent)
    rows = conn.execute("PRAGMA table_info(cards)").fetchall()
    if rows:
        conn.execute("UPDATE cards SET tags = REPLACE(tags, ',', ' ') WHERE tags LIKE '%,%'")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)


# --- Filter helper ---


def _build_card_filter_clauses(
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leech_threshold: int | None = None,
) -> tuple[str, list[str | int]]:
    """Build SQL WHERE fragments for card filtering.

    Returns (sql_fragment, params). The fragment uses AND-prefixed clauses
    suitable for appending to an existing WHERE.
    """
    clauses: list[str] = []
    params: list[str | int] = []

    if deck is not None:
        clauses.append("AND d.name = ?")
        params.append(deck)

    if tags:
        tag_conditions: list[str] = []
        for tag in tags:
            tag_conditions.append(
                "((' ' || c.tags || ' ') LIKE ? OR (' ' || c.tags || ' ') LIKE ?)"
            )
            params.extend([f"% {tag} %", f"% {tag}::%"])
        clauses.append(f"AND ({' OR '.join(tag_conditions)})")

    if state is not None:
        if state == "new":
            clauses.append("AND (fs.state = ? AND fs.last_review IS NULL)")
            params.append(State.Learning.value)
        elif state == "learning":
            clauses.append("AND (fs.state = ? AND fs.last_review IS NOT NULL)")
            params.append(State.Learning.value)
        elif state == "review":
            clauses.append("AND fs.state = ?")
            params.append(State.Review.value)
        elif state == "relearning":
            clauses.append("AND fs.state = ?")
            params.append(State.Relearning.value)

    if leech_threshold is not None:
        clauses.append("AND fs.lapse_count >= ?")
        params.append(leech_threshold)

    return (" ".join(clauses), params)


# --- Deck operations ---


def upsert_deck(conn: sqlite3.Connection, name: str, source_id: int | None = None) -> int:
    """INSERT OR IGNORE by name, return deck ID."""
    conn.execute(
        "INSERT OR IGNORE INTO decks (name, source_id) VALUES (?, ?)",
        (name, source_id),
    )
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def list_decks(conn: sqlite3.Connection) -> list[DeckInfo]:
    """List all decks with card counts and due counts."""
    rows = conn.execute("""
        SELECT d.name,
               COUNT(c.id) AS card_count,
               COUNT(CASE WHEN fs.due <= datetime('now') AND c.suspended = 0
                          THEN 1 END) AS due_count
        FROM decks d
        LEFT JOIN cards c ON c.deck_id = d.id
        LEFT JOIN fsrs_state fs ON fs.card_id = c.id
        GROUP BY d.id, d.name
        ORDER BY d.name
    """).fetchall()
    return [
        DeckInfo(name=r["name"], card_count=r["card_count"], due_count=r["due_count"]) for r in rows
    ]


def get_deck_records(conn: sqlite3.Connection) -> list[DeckRecord]:
    """Get all decks as DeckRecord (with IDs)."""
    rows = conn.execute(
        "SELECT id, name, source_id, created_at FROM decks ORDER BY name"
    ).fetchall()
    return [
        DeckRecord(id=r["id"], name=r["name"], source_id=r["source_id"], created_at=r["created_at"])
        for r in rows
    ]


# --- Card operations ---


def insert_card(conn: sqlite3.Connection, card: CardRecord) -> tuple[int, bool]:
    """Insert a card with dedup on source_note_id.

    Returns (card_id, was_update) — was_update is True if an existing card
    was updated via source_note_id dedup.
    """
    extra_json = json.dumps(card.extra_fields)

    if card.source_note_id is not None:
        existing = conn.execute(
            "SELECT id FROM cards WHERE source_note_id = ?",
            (card.source_note_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE cards SET question = ?, answer = ?, extra_fields = ?,
                   tags = ?, source_note_guid = ?
                   WHERE id = ?""",
                (
                    card.question,
                    card.answer,
                    extra_json,
                    card.tags,
                    card.source_note_guid,
                    existing["id"],
                ),
            )
            return (int(existing["id"]), True)

    cursor = conn.execute(
        """INSERT INTO cards (deck_id, question, answer, extra_fields, tags, source,
           source_note_id, source_note_guid, suspended)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card.deck_id,
            card.question,
            card.answer,
            extra_json,
            card.tags,
            card.source,
            card.source_note_id,
            card.source_note_guid,
            int(card.suspended),
        ),
    )
    card_id = cursor.lastrowid
    if card_id is None:
        msg = "Failed to insert card"
        raise RuntimeError(msg)
    insert_initial_fsrs_state(conn, card_id)
    return (card_id, False)


def get_next_due_card(
    conn: sqlite3.Connection,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
) -> CardDue | None:
    """Get the next due card, optionally filtered. Returns None when nothing is due."""
    filter_sql, filter_params = _build_card_filter_clauses(deck=deck, tags=tags, state=state)
    query = f"""
        SELECT c.id AS card_id, c.question, c.answer, d.name AS deck,
               c.tags, c.extra_fields,
               fs.state, fs.last_review, fs.fsrs_card_json
        FROM cards c
        JOIN fsrs_state fs ON fs.card_id = c.id
        JOIN decks d ON d.id = c.deck_id
        WHERE fs.due <= datetime('now') AND c.suspended = 0
        {filter_sql}
        ORDER BY fs.due ASC
        LIMIT 1
    """
    row = conn.execute(query, filter_params).fetchone()
    if row is None:
        return None

    fsrs_card = fsrs_engine.deserialize_card(row["fsrs_card_json"])
    retrievability = fsrs_engine.get_retrievability(fsrs_card)
    card_state = fsrs_engine.state_name(State(row["state"]), row["last_review"])
    extra_parsed: dict[str, str] = json.loads(row["extra_fields"]) if row["extra_fields"] else {}  # type: ignore[assignment]  # json.loads returns Any

    return CardDue(
        card_id=row["card_id"],
        question=row["question"],
        answer=row["answer"],
        deck=row["deck"],
        tags=row["tags"],
        state=card_state,
        retrievability=round(retrievability, 4),
        extra_fields=extra_parsed,
    )


def list_cards(
    conn: sqlite3.Connection,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leech_threshold: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> CardListResult:
    """List cards with optional filters, paginated."""
    filter_sql, filter_params = _build_card_filter_clauses(
        deck=deck, tags=tags, state=state, leech_threshold=leech_threshold
    )

    count_query = f"""
        SELECT COUNT(*) AS cnt
        FROM cards c
        JOIN fsrs_state fs ON fs.card_id = c.id
        JOIN decks d ON d.id = c.deck_id
        WHERE 1=1 {filter_sql}
    """
    count_row = conn.execute(count_query, filter_params).fetchone()
    total = int(count_row["cnt"]) if count_row else 0

    data_query = f"""
        SELECT c.id AS card_id, c.question, d.name AS deck, c.tags,
               c.suspended, fs.state, fs.last_review, fs.due, fs.lapse_count
        FROM cards c
        JOIN fsrs_state fs ON fs.card_id = c.id
        JOIN decks d ON d.id = c.deck_id
        WHERE 1=1 {filter_sql}
        ORDER BY c.id ASC
        LIMIT ? OFFSET ?
    """
    data_params: list[str | int] = [*filter_params, limit, offset]
    rows = conn.execute(data_query, data_params).fetchall()

    cards = [
        CardSummary(
            card_id=r["card_id"],
            question=r["question"][:100],
            deck=r["deck"],
            tags=r["tags"],
            state=fsrs_engine.state_name(State(r["state"]), r["last_review"]),
            due=r["due"],
            suspended=bool(r["suspended"]),
            lapse_count=r["lapse_count"],
        )
        for r in rows
    ]
    return CardListResult(cards=cards, total=total, limit=limit, offset=offset)


def get_card_detail(conn: sqlite3.Connection, card_id: int) -> CardDetail | None:
    """Get full card detail including FSRS state and review count."""
    row = conn.execute(
        """
        SELECT c.id AS card_id, c.question, c.answer, d.name AS deck,
               c.tags, c.extra_fields, c.source, c.suspended, c.created_at,
               fs.state, fs.due, fs.stability, fs.difficulty,
               fs.last_review, fs.fsrs_card_json, fs.lapse_count,
               (SELECT COUNT(*) FROM review_logs rl WHERE rl.card_id = c.id) AS review_count
        FROM cards c
        JOIN fsrs_state fs ON fs.card_id = c.id
        JOIN decks d ON d.id = c.deck_id
        WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    if row is None:
        return None

    fsrs_card = fsrs_engine.deserialize_card(row["fsrs_card_json"])
    retrievability = fsrs_engine.get_retrievability(fsrs_card)
    extra_parsed: dict[str, str] = json.loads(row["extra_fields"]) if row["extra_fields"] else {}  # type: ignore[assignment]  # json.loads returns Any
    return CardDetail(
        card_id=row["card_id"],
        question=row["question"],
        answer=row["answer"],
        deck=row["deck"],
        tags=row["tags"],
        extra_fields=extra_parsed,
        source=row["source"],
        suspended=bool(row["suspended"]),
        created_at=row["created_at"],
        state=fsrs_engine.state_name(State(row["state"]), row["last_review"]),
        due=row["due"],
        stability=round(float(row["stability"] or 0.0), 4),
        difficulty=round(float(row["difficulty"] or 0.0), 4),
        retrievability=round(retrievability, 4),
        last_review=row["last_review"],
        review_count=row["review_count"],
        lapse_count=row["lapse_count"],
    )


def delete_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Delete a card and its related data. Returns False if not found."""
    existing = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existing is None:
        return False
    conn.execute("DELETE FROM review_logs WHERE card_id = ?", (card_id,))
    conn.execute("DELETE FROM fsrs_state WHERE card_id = ?", (card_id,))
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    return True


def update_card(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    question: str | None = None,
    answer: str | None = None,
    tags: str | None = None,
    deck_id: int | None = None,
) -> bool:
    """Update card fields. Only non-None values are changed. Returns False if not found."""
    existing = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existing is None:
        return False

    updates: list[str] = []
    params: list[str | int] = []
    if question is not None:
        updates.append("question = ?")
        params.append(question)
    if answer is not None:
        updates.append("answer = ?")
        params.append(answer)
    if tags is not None:
        updates.append("tags = ?")
        params.append(tags)
    if deck_id is not None:
        updates.append("deck_id = ?")
        params.append(deck_id)

    if updates:
        params.append(card_id)
        conn.execute(
            f"UPDATE cards SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return True


def suspend_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Suspend a card. Returns False if not found."""
    cursor = conn.execute("UPDATE cards SET suspended = 1 WHERE id = ?", (card_id,))
    return cursor.rowcount > 0


def unsuspend_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Unsuspend a card. Returns False if not found."""
    cursor = conn.execute("UPDATE cards SET suspended = 0 WHERE id = ?", (card_id,))
    return cursor.rowcount > 0


def increment_lapse_count(conn: sqlite3.Connection, card_id: int) -> int:
    """Increment lapse count for a card. Returns the new count."""
    conn.execute(
        "UPDATE fsrs_state SET lapse_count = lapse_count + 1 WHERE card_id = ?",
        (card_id,),
    )
    row = conn.execute(
        "SELECT lapse_count FROM fsrs_state WHERE card_id = ?", (card_id,)
    ).fetchone()
    return int(row["lapse_count"])


def get_all_cards(conn: sqlite3.Connection, deck_id: int | None = None) -> list[CardRecord]:
    """Get all cards, optionally filtered by deck."""
    if deck_id is not None:
        rows = conn.execute("SELECT * FROM cards WHERE deck_id = ?", (deck_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards").fetchall()
    return [_row_to_card_record(r) for r in rows]


# --- FSRS state operations ---


def get_fsrs_card(conn: sqlite3.Connection, card_id: int) -> Card | None:
    """Deserialize the FSRS card for a given card_id."""
    row = conn.execute(
        "SELECT fsrs_card_json FROM fsrs_state WHERE card_id = ?", (card_id,)
    ).fetchone()
    if row is None:
        return None
    return fsrs_engine.deserialize_card(row["fsrs_card_json"])


def update_fsrs_state(
    conn: sqlite3.Connection, card_id: int, fsrs_card: Card, retrievability: float
) -> None:
    """Update FSRS state with serialized card and denormalized columns."""
    last_review_str: str | None = None
    if fsrs_card.last_review is not None:
        last_review_str = _to_sqlite_dt(fsrs_card.last_review)

    due_str = _to_sqlite_dt(fsrs_card.due) if fsrs_card.due else _to_sqlite_dt(datetime.now(UTC))

    conn.execute(
        """UPDATE fsrs_state
           SET fsrs_card_json = ?, due = ?, state = ?, stability = ?,
               difficulty = ?, last_review = ?, retrievability = ?
           WHERE card_id = ?""",
        (
            fsrs_engine.serialize_card(fsrs_card),
            due_str,
            fsrs_card.state.value,
            fsrs_card.stability,
            fsrs_card.difficulty,
            last_review_str,
            retrievability,
            card_id,
        ),
    )


def insert_initial_fsrs_state(conn: sqlite3.Connection, card_id: int) -> None:
    """Create an FSRS state row with a fresh card."""
    card = fsrs_engine.create_new_card()
    due_str = _to_sqlite_dt(card.due) if card.due else _to_sqlite_dt(datetime.now(UTC))
    conn.execute(
        """INSERT INTO fsrs_state (card_id, fsrs_card_json, due, state, stability,
           difficulty, last_review, retrievability)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            fsrs_engine.serialize_card(card),
            due_str,
            card.state.value,
            card.stability,
            card.difficulty,
            None,
            0.0,
        ),
    )


# --- Review / stats operations ---


def insert_review_log(conn: sqlite3.Connection, review: ReviewInput, fsrs_log_json: str) -> None:
    """Insert a review log entry."""
    conn.execute(
        """INSERT INTO review_logs (card_id, rating, user_answer, feedback,
           session_id, fsrs_log_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            review.card_id,
            review.rating,
            review.user_answer,
            review.feedback,
            review.session_id,
            fsrs_log_json,
        ),
    )


def get_due_count(conn: sqlite3.Connection) -> DueCount:
    """Count due cards grouped by state."""
    rows = conn.execute("""
        SELECT fs.state,
               CASE WHEN fs.last_review IS NULL THEN 1 ELSE 0 END AS is_new,
               COUNT(*) AS cnt
        FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE fs.due <= datetime('now') AND c.suspended = 0
        GROUP BY fs.state, is_new
    """).fetchall()

    new = 0
    learning = 0
    review = 0
    for r in rows:
        state_val = r["state"]
        cnt = r["cnt"]
        if state_val == State.Learning.value and r["is_new"]:
            new += cnt
        elif state_val == State.Learning.value:
            learning += cnt
        elif state_val == State.Review.value:
            review += cnt
        else:  # Relearning
            learning += cnt

    return DueCount(total_due=new + learning + review, learning=learning, review=review, new=new)


def get_next_due_time(conn: sqlite3.Connection) -> str | None:
    """Get the next due time for any non-suspended card after now."""
    row = conn.execute("""
        SELECT MIN(fs.due) AS next_due
        FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE fs.due > datetime('now') AND c.suspended = 0
    """).fetchone()
    if row is None or row["next_due"] is None:
        return None
    return str(row["next_due"])


def get_session_stats(conn: sqlite3.Connection, session_id: str) -> SessionStats:
    """Get stats for a specific review session."""
    rows = conn.execute(
        "SELECT rating, COUNT(*) AS cnt FROM review_logs WHERE session_id = ? GROUP BY rating",
        (session_id,),
    ).fetchall()

    again = hard = good = easy = 0
    for r in rows:
        rating = int(r["rating"])
        match rating:
            case 1:
                again = int(r["cnt"])
            case 2:
                hard = int(r["cnt"])
            case 3:
                good = int(r["cnt"])
            case 4:
                easy = int(r["cnt"])
            case _:
                pass

    reviewed = again + hard + good + easy
    accuracy = (good + easy) / reviewed if reviewed > 0 else 0.0
    return SessionStats(
        reviewed=reviewed,
        again=again,
        hard=hard,
        good=good,
        easy=easy,
        accuracy=round(accuracy, 4),
    )


def get_overall_stats(conn: sqlite3.Connection) -> OverallStats:
    """Get overall database statistics."""
    total = conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
    total_cards = total["cnt"] if total else 0

    due_row = conn.execute("""
        SELECT COUNT(*) AS cnt FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE fs.due <= datetime('now') AND c.suspended = 0
    """).fetchone()
    due_now = due_row["cnt"] if due_row else 0

    state_rows = conn.execute("""
        SELECT fs.state,
               CASE WHEN fs.last_review IS NULL THEN 1 ELSE 0 END AS is_new,
               COUNT(*) AS cnt
        FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE c.suspended = 0
        GROUP BY fs.state, is_new
    """).fetchall()

    learning = 0
    review = 0
    for r in state_rows:
        if r["state"] == State.Learning.value and not r["is_new"]:
            learning += r["cnt"]
        elif r["state"] == State.Review.value:
            review += r["cnt"]
        elif r["state"] == State.Relearning.value:
            learning += r["cnt"]

    mature_row = conn.execute("""
        SELECT COUNT(*) AS cnt FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE fs.stability IS NOT NULL AND fs.stability > 21 AND c.suspended = 0
    """).fetchone()
    mature = mature_row["cnt"] if mature_row else 0

    avg_ret_row = conn.execute("""
        SELECT AVG(fs.retrievability) AS avg_ret FROM fsrs_state fs
        JOIN cards c ON c.id = fs.card_id
        WHERE fs.retrievability IS NOT NULL AND fs.last_review IS NOT NULL
              AND c.suspended = 0
    """).fetchone()
    avg_retention = avg_ret_row["avg_ret"] if avg_ret_row and avg_ret_row["avg_ret"] else 0.0

    return OverallStats(
        total_cards=total_cards,
        due_now=due_now,
        learning=learning,
        review=review,
        mature=mature,
        avg_retention=round(float(avg_retention), 4),
    )


# --- Config operations ---


def get_config(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a config value by key."""
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a config value (upsert)."""
    conn.execute(
        """INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')""",
        (key, value, value),
    )


# --- Review log queries ---


def get_all_review_log_jsons(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Returns (card_id, fsrs_log_json) for all reviews with non-null JSON."""
    rows = conn.execute(
        "SELECT card_id, fsrs_log_json FROM review_logs WHERE fsrs_log_json IS NOT NULL"
    ).fetchall()
    return [(r["card_id"], r["fsrs_log_json"]) for r in rows]


def get_review_logs_for_card(conn: sqlite3.Connection, card_id: int) -> list[str]:
    """Get fsrs_log_json strings for a card, ordered by review time."""
    rows = conn.execute(
        """SELECT fsrs_log_json FROM review_logs
           WHERE card_id = ? AND fsrs_log_json IS NOT NULL
           ORDER BY reviewed_at""",
        (card_id,),
    ).fetchall()
    return [r["fsrs_log_json"] for r in rows]


# --- Tag operations ---


def list_tags(conn: sqlite3.Connection) -> list[str]:
    """Return all unique tags from the database, sorted alphabetically."""
    rows = conn.execute("SELECT tags FROM cards WHERE tags != ''").fetchall()
    tag_set: set[str] = set()
    for row in rows:
        for tag in row["tags"].split():
            tag_set.add(tag)
    return sorted(tag_set)


# --- Helpers ---


def _row_to_card_record(row: sqlite3.Row) -> CardRecord:
    """Convert a database row to a CardRecord."""
    extra_parsed: dict[str, str] = json.loads(row["extra_fields"]) if row["extra_fields"] else {}  # type: ignore[assignment]  # json.loads returns Any
    return CardRecord(
        id=row["id"],
        deck_id=row["deck_id"],
        question=row["question"],
        answer=row["answer"],
        extra_fields=extra_parsed,
        tags=row["tags"],
        source=row["source"],
        source_note_id=row["source_note_id"],
        source_note_guid=row["source_note_guid"],
        created_at=row["created_at"],
        suspended=bool(row["suspended"]),
    )
