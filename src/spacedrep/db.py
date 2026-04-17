"""SQLite queries against Anki-native schema.

All queries target the Anki tables (col, notes, cards, revlog, graves)
plus spacedrep extension tables (spacedrep_meta, spacedrep_card_extra,
spacedrep_review_extra). FSRS state is stored in Anki's native card
columns — no separate fsrs_state table.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fsrs import Card

from spacedrep import fsrs_engine
from spacedrep.anki_render import (
    ModelInfo,
    render_card,
    resolve_card_qa_fields,
)
from spacedrep.anki_schema import (
    ANKI_SCHEMA,
    ColMeta,
    anki_fields_to_fsrs_card,
    due_to_datetime,
    fsrs_card_to_anki_fields,
)
from spacedrep.models import (
    CardDetail,
    CardDue,
    CardListResult,
    CardSource,
    CardSummary,
    DeckInfo,
    DueCount,
    OverallStats,
    ReviewInput,
    ReviewLogEntry,
    SessionStats,
)

VALID_STATES = frozenset({"new", "learning", "review", "relearning"})


class ClozeUpdateNotSupportedError(Exception):
    """Raised by update_card when called on a cloze card.

    Core translates this to UpdateClozeCardError for the CLI/MCP surface.
    Cloze notes should be edited via update_cloze_note, which rewrites the
    full Text field and regenerates cards per {{c1::...}} marker.
    """

    def __init__(self, card_id: int) -> None:
        super().__init__(f"Cloze update not supported for card {card_id}")
        self.card_id = card_id


class ModernSchemaError(Exception):
    """Raised when a DB uses Anki 2.1.49+ split-table schema.

    Core translates this to UnsupportedCollectionFormatError for the
    CLI/MCP surface. Data in those DBs lives in notetypes/templates/
    fields/deck_config/config/tags tables; the legacy col.* JSON
    columns are empty strings, which our reader can't interpret.
    """


# Monotonic ID counter to avoid collisions within the same millisecond
_next_id_counter = 0


def next_id() -> int:
    """Generate a unique timestamp-based ID (milliseconds + counter)."""
    global _next_id_counter
    ts = int(time.time() * 1000)
    _next_id_counter += 1
    return ts + _next_id_counter


def _now_str() -> str:
    """Current UTC time as ISO datetime string."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Connection and initialization
# ---------------------------------------------------------------------------


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_TABLE_COUNT = 8  # 5 Anki + 3 extension


def init_db(conn: sqlite3.Connection) -> int:
    """Create Anki schema and insert default col row. Returns table count."""
    conn.executescript(ANKI_SCHEMA)
    existing = conn.execute("SELECT id FROM col WHERE id = 1").fetchone()
    if existing is None:
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
    return _TABLE_COUNT


# ---------------------------------------------------------------------------
# ColMeta helpers (read/write col row)
# ---------------------------------------------------------------------------


def load_col_meta(conn: sqlite3.Connection) -> ColMeta:
    """Read and parse the col row."""
    row = conn.execute(
        "SELECT crt, mod, scm, ver, conf, models, decks, dconf, tags FROM col WHERE id = 1"
    ).fetchone()
    if row is None:
        msg = "No col row found — database not initialized"
        raise RuntimeError(msg)
    return ColMeta.from_row(dict(row))


def save_col_meta(conn: sqlite3.Connection, meta: ColMeta) -> None:
    """Write the col row back to the database."""
    meta.mod = int(time.time())
    conn.execute(
        "UPDATE col SET mod=?, conf=?, models=?, decks=?, dconf=?, tags=? WHERE id=1",
        (
            meta.mod,
            json.dumps(meta.conf),
            json.dumps(meta.models),
            json.dumps(meta.decks),
            json.dumps(meta.dconf),
            json.dumps(meta.tags),
        ),
    )


def _get_crt(conn: sqlite3.Connection) -> int:
    """Get col.crt (collection creation timestamp)."""
    row = conn.execute("SELECT crt FROM col WHERE id = 1").fetchone()
    if row is None:
        msg = "No col row found"
        raise RuntimeError(msg)
    return int(row["crt"])


# ---------------------------------------------------------------------------
# Model info cache (per-connection)
# ---------------------------------------------------------------------------


_model_cache: dict[int, dict[str, ModelInfo]] = {}


def is_modern_anki_schema(conn: sqlite3.Connection) -> bool:
    """True iff col.models is empty AND the notetypes table has rows.

    Signal that this DB uses Anki 2.1.49+ split-table layout: note-type
    metadata lives in notetypes/templates/fields instead of col.models.
    spacedrep reads only the legacy col.* JSON columns today, so these
    DBs must be rejected at the boundary with a clear error.

    Returns False for:
        - brand-new SQLite files with no col table yet (pre-init_db),
        - fresh DBs where col row exists with populated JSON,
        - fresh DBs with no col row yet,
        - legacy-shaped DBs that happen to have empty col.models but no
          notetypes table (don't false-positive).
    """
    has_col = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='col'"
    ).fetchone()
    if has_col is None:
        return False
    row = conn.execute("SELECT models FROM col WHERE id = 1").fetchone()
    if row is None or row["models"]:
        return False
    has_nt = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notetypes'"
    ).fetchone()
    if has_nt is None:
        return False
    nt_count = conn.execute("SELECT COUNT(*) AS n FROM notetypes").fetchone()
    return bool(nt_count and int(nt_count["n"]) > 0)


def _get_models(conn: sqlite3.Connection) -> dict[str, ModelInfo]:
    """Get parsed model info, cached per connection id."""
    conn_id = id(conn)
    if conn_id in _model_cache:
        return _model_cache[conn_id]

    row = conn.execute("SELECT models FROM col WHERE id = 1").fetchone()
    if row is None:
        return {}
    models_json: dict[str, Any] = json.loads(row["models"])
    result: dict[str, ModelInfo] = {}
    for mid, model in models_json.items():
        result[mid] = ModelInfo(
            field_names=[f["name"] for f in model["flds"]],
            templates=model.get("tmpls", []),
            model_type=model.get("type", 0),
        )
    _model_cache[conn_id] = result
    return result


def invalidate_model_cache(conn: sqlite3.Connection) -> None:
    """Clear the model cache for a connection (call after modifying col.models)."""
    _model_cache.pop(id(conn), None)


def clear_all_model_caches() -> None:
    """Clear all model caches. For test cleanup."""
    _model_cache.clear()


# ---------------------------------------------------------------------------
# Deck operations
# ---------------------------------------------------------------------------


def upsert_deck(conn: sqlite3.Connection, name: str, source_id: int | None = None) -> int:
    """Get or create a deck by name. Returns the deck ID.

    source_id is ignored in the new schema (kept for API compat).
    """
    meta = load_col_meta(conn)
    deck_id = meta.ensure_deck(name)
    save_col_meta(conn, meta)
    return deck_id


def list_decks(conn: sqlite3.Connection) -> list[DeckInfo]:
    """List all decks with card counts and due counts."""
    meta = load_col_meta(conn)
    crt = meta.crt
    now_ts = int(time.time())
    now_days = int((now_ts - crt) / 86400)

    results: list[DeckInfo] = []
    for did_str, deck_data in meta.decks.items():
        did = int(did_str)
        deck_name = str(deck_data.get("name", "Unknown"))

        card_count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM cards WHERE did = ?", (did,)
        ).fetchone()
        card_count = int(card_count_row["cnt"]) if card_count_row else 0

        # Due count: type-dependent check
        due_row = conn.execute(
            """SELECT COUNT(*) AS cnt FROM cards c
            LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
            WHERE c.did = ? AND c.queue >= 0
            AND (sce.buried_until IS NULL OR sce.buried_until <= ?)
            AND (
                (c.type = 0 AND c.queue = 0)
                OR (c.type IN (1, 3) AND c.due <= ?)
                OR (c.type = 2 AND c.due <= ?)
            )""",
            (did, _now_str(), now_ts, now_days),
        ).fetchone()
        due_count = int(due_row["cnt"]) if due_row else 0

        results.append(DeckInfo(name=deck_name, card_count=card_count, due_count=due_count))

    results.sort(key=lambda d: d.name)
    return results


# ---------------------------------------------------------------------------
# Card insert
# ---------------------------------------------------------------------------


def insert_card(
    conn: sqlite3.Connection,
    *,
    question: str,
    answer: str,
    deck_name: str,
    tags: str = "",
    model_type: str = "basic",
    source: CardSource = "manual",
    guid: str,
    note_flds: str | None = None,
) -> tuple[int, bool]:
    """Insert a card (note + card row). Dedup on notes.guid.

    Returns (card_id, was_update).
    """
    now = int(time.time())

    meta = load_col_meta(conn)
    did = meta.ensure_deck(deck_name)
    mid = meta.ensure_model(model_type)
    save_col_meta(conn, meta)

    # Build flds
    if note_flds is None:
        note_flds = f"{question}\x1f" if model_type == "cloze" else f"{question}\x1f{answer}"

    sfld = question

    # Dedup on guid
    existing = conn.execute("SELECT id FROM notes WHERE guid = ?", (guid,)).fetchone()
    if existing:
        note_id = int(existing["id"])
        if tags:
            # Explicit tags provided — update them
            conn.execute(
                "UPDATE notes SET flds=?, sfld=?, tags=?, mod=?, usn=-1 WHERE id=?",
                (note_flds, sfld, tags, now, note_id),
            )
        else:
            # Empty tags = "not specified"; preserve existing. Use update_card to clear.
            conn.execute(
                "UPDATE notes SET flds=?, sfld=?, mod=?, usn=-1 WHERE id=?",
                (note_flds, sfld, now, note_id),
            )
        # Update card's deck if changed
        conn.execute("UPDATE cards SET did=?, mod=?, usn=-1 WHERE nid=?", (did, now, note_id))

        card_row = conn.execute(
            "SELECT id FROM cards WHERE nid = ? ORDER BY ord LIMIT 1", (note_id,)
        ).fetchone()
        card_id = int(card_row["id"]) if card_row else note_id
        return (card_id, True)

    # Insert new note
    note_id = next_id()
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, '')",
        (note_id, guid, mid, now, tags, note_flds, sfld),
    )

    # Get next position for new cards
    pos_row = conn.execute("SELECT MAX(due) AS maxdue FROM cards WHERE type = 0").fetchone()
    position = (int(pos_row["maxdue"]) + 1) if (pos_row and pos_row["maxdue"] is not None) else 0

    # Insert card
    card_id = next_id()
    conn.execute(
        "INSERT INTO cards"
        " (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
        "  factor, reps, lapses, left, odue, odid, flags, data)"
        " VALUES (?, ?, ?, 0, ?, -1, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
        (card_id, note_id, did, now, position),
    )

    # Insert source into extension table
    if source != "manual":
        conn.execute(
            "INSERT INTO spacedrep_card_extra (card_id, source) VALUES (?, ?)"
            " ON CONFLICT(card_id) DO UPDATE SET source = excluded.source",
            (card_id, source),
        )

    invalidate_model_cache(conn)
    return (card_id, False)


def insert_cloze_cards(
    conn: sqlite3.Connection,
    *,
    note_id: int,
    did: int,
    ordinals: list[int],
    now: int,
) -> list[int]:
    """Insert card rows for a cloze note (one per ordinal). Returns card IDs."""
    pos_row = conn.execute("SELECT MAX(due) AS maxdue FROM cards WHERE type = 0").fetchone()
    position = (int(pos_row["maxdue"]) + 1) if (pos_row and pos_row["maxdue"] is not None) else 0

    card_ids: list[int] = []
    for idx, ord_val in enumerate(ordinals):
        card_id = next_id()
        conn.execute(
            "INSERT INTO cards"
            " (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
            "  factor, reps, lapses, left, odue, odid, flags, data)"
            " VALUES (?, ?, ?, ?, ?, -1, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (card_id, note_id, did, ord_val, now, position + idx),
        )
        card_ids.append(card_id)
    return card_ids


def insert_reversed_note(
    conn: sqlite3.Connection,
    *,
    question: str,
    answer: str,
    deck_name: str,
    tags: str = "",
    source: CardSource = "manual",
    guid: str,
) -> tuple[int, list[int], bool]:
    """Insert or update a reversed note (1 note + 2 cards, ord=[0, 1]).

    Dedup on notes.guid. On update, the note's Question/Answer fields are
    rewritten (both rendered cards reflect the change), and the deck is
    moved to match. Any missing ordinal among {0, 1} is filled in so the
    pair is always intact.

    Returns (note_id, [card_id_ord0, card_id_ord1], was_update).
    """
    now = int(time.time())

    meta = load_col_meta(conn)
    did = meta.ensure_deck(deck_name)
    mid = meta.ensure_model("reversed")
    save_col_meta(conn, meta)

    note_flds = f"{question}\x1f{answer}"
    sfld = question

    existing = conn.execute("SELECT id FROM notes WHERE guid = ?", (guid,)).fetchone()
    if existing:
        note_id = int(existing["id"])
        if tags:
            conn.execute(
                "UPDATE notes SET flds=?, sfld=?, tags=?, mod=?, usn=-1 WHERE id=?",
                (note_flds, sfld, tags, now, note_id),
            )
        else:
            # Empty tags = "not specified" — preserve existing.
            conn.execute(
                "UPDATE notes SET flds=?, sfld=?, mod=?, usn=-1 WHERE id=?",
                (note_flds, sfld, now, note_id),
            )
        conn.execute(
            "UPDATE cards SET did=?, mod=?, usn=-1 WHERE nid=?",
            (did, now, note_id),
        )

        existing_cards = conn.execute(
            "SELECT id, ord FROM cards WHERE nid = ? ORDER BY ord",
            (note_id,),
        ).fetchall()
        by_ord: dict[int, int] = {int(r["ord"]): int(r["id"]) for r in existing_cards}
        missing = [o for o in (0, 1) if o not in by_ord]
        if missing:
            new_ids = insert_cloze_cards(conn, note_id=note_id, did=did, ordinals=missing, now=now)
            for ord_val, cid in zip(missing, new_ids, strict=True):
                by_ord[ord_val] = cid
                if source != "manual":
                    conn.execute(
                        "INSERT INTO spacedrep_card_extra (card_id, source) VALUES (?, ?)"
                        " ON CONFLICT(card_id) DO UPDATE SET source = excluded.source",
                        (cid, source),
                    )

        invalidate_model_cache(conn)
        return (note_id, [by_ord[0], by_ord[1]], True)

    # New note + 2 cards
    note_id = next_id()
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, '')",
        (note_id, guid, mid, now, tags, note_flds, sfld),
    )
    card_ids = insert_cloze_cards(conn, note_id=note_id, did=did, ordinals=[0, 1], now=now)
    if source != "manual":
        for cid in card_ids:
            conn.execute(
                "INSERT INTO spacedrep_card_extra (card_id, source) VALUES (?, ?)"
                " ON CONFLICT(card_id) DO UPDATE SET source = excluded.source",
                (cid, source),
            )

    invalidate_model_cache(conn)
    return (note_id, card_ids, False)


# ---------------------------------------------------------------------------
# Card read operations
# ---------------------------------------------------------------------------


def _render_from_row(
    row: sqlite3.Row, models: dict[str, ModelInfo]
) -> tuple[str, str, dict[str, str]]:
    """Render question/answer/extra from a row with flds, mid, ord columns."""
    mid_str = str(row["mid"])
    minfo = models.get(mid_str)
    if minfo is None:
        # Fallback: split flds and take first two
        parts = str(row["flds"]).split("\x1f")
        return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "", {})
    return render_card(str(row["flds"]), minfo, int(row["ord"]))


def card_state_name(card_type: int, last_review_ts: int | None) -> str:
    """Convert Anki card type to human-readable state name."""
    if card_type == 0:
        return "new"
    state = {0: "new", 1: "learning", 2: "review", 3: "relearning"}.get(card_type, "learning")
    if card_type == 1 and last_review_ts is None:
        return "new"
    return state


def get_last_review_ts(data_str: str) -> int | None:
    """Extract lrt from cards.data JSON, or None."""
    if not data_str:
        return None
    try:
        data = json.loads(data_str)
        lrt = data.get("lrt")
        return int(lrt) if lrt is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def get_next_due_card(
    conn: sqlite3.Connection,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    search: str | None = None,
    due_before: str | None = None,
) -> CardDue | None:
    """Get the next due card. Returns None when nothing is due."""
    crt = _get_crt(conn)
    models = _get_models(conn)
    now_ts = int(time.time())
    now_days = int((now_ts - crt) / 86400)

    filter_sql, filter_params = _build_card_filter_clauses(
        deck=deck,
        tags=tags,
        state=state,
        search=search,
        due_before=due_before,
        crt=crt,
    )

    query = f"""
        SELECT c.id AS card_id, n.flds, n.mid, c.ord, n.tags,
               c.type, c.due, c.ivl, c.factor, c.data, c.did,
               sce.step
        FROM cards c
        JOIN notes n ON n.id = c.nid
        LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
        WHERE c.queue >= 0
        AND (sce.buried_until IS NULL OR sce.buried_until <= ?)
        AND (
            (c.type = 0 AND c.queue = 0)
            OR (c.type IN (1, 3) AND c.due <= ?)
            OR (c.type = 2 AND c.due <= ?)
        )
        {filter_sql}
        ORDER BY
            CASE WHEN c.type IN (1, 3) THEN 0 ELSE 1 END,
            c.due ASC
        LIMIT 1
    """
    params: list[Any] = [_now_str(), now_ts, now_days, *filter_params]
    row = conn.execute(query, params).fetchone()
    if row is None:
        return None

    question, answer, extra = _render_from_row(row, models)
    deck_name = deck_name_for_did(conn, row["did"])
    lrt = get_last_review_ts(row["data"])

    # Get FSRS card for retrievability
    fsrs_card = anki_fields_to_fsrs_card(dict(row), crt)
    retrievability = fsrs_engine.get_retrievability(fsrs_card)

    return CardDue(
        card_id=row["card_id"],
        question=question,
        answer=answer,
        deck=deck_name,
        tags=row["tags"],
        state=card_state_name(row["type"], lrt),
        retrievability=round(retrievability, 4),
        extra_fields=extra,
    )


def list_cards(
    conn: sqlite3.Connection,
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leech_threshold: int | None = None,
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
    crt = _get_crt(conn)
    models = _get_models(conn)

    filter_sql, filter_params = _build_card_filter_clauses(
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
        crt=crt,
    )

    base_from = f"""
        FROM cards c
        JOIN notes n ON n.id = c.nid
        LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
        WHERE 1=1 {filter_sql}
    """

    needs_retrievability = min_retrievability is not None or max_retrievability is not None

    select_cols = """
        SELECT c.id AS card_id, n.flds, n.mid, c.ord, n.tags,
               c.type, c.due, c.data, c.did, c.queue, c.lapses,
               c.ivl, c.factor, sce.buried_until, sce.step
    """

    if needs_retrievability:
        # Fetch all matching rows, filter by retrievability in Python, then paginate
        data_query = f"{select_cols} {base_from} ORDER BY c.id ASC"
        all_rows = conn.execute(data_query, filter_params).fetchall()

        filtered_rows: list[sqlite3.Row] = []
        for r in all_rows:
            fsrs_card = anki_fields_to_fsrs_card(dict(r), crt)
            ret = fsrs_engine.get_retrievability(fsrs_card)
            if min_retrievability is not None and ret < min_retrievability:
                continue
            if max_retrievability is not None and ret > max_retrievability:
                continue
            filtered_rows.append(r)

        total = len(filtered_rows)
        rows = filtered_rows[offset : offset + limit]
    else:
        count_row = conn.execute(f"SELECT COUNT(*) AS cnt {base_from}", filter_params).fetchone()
        total = int(count_row["cnt"]) if count_row else 0

        data_query = f"{select_cols} {base_from} ORDER BY c.id ASC LIMIT ? OFFSET ?"
        data_params: list[Any] = [*filter_params, limit, offset]
        rows = conn.execute(data_query, data_params).fetchall()

    cards: list[CardSummary] = []
    for r in rows:
        question, _, _ = _render_from_row(r, models)
        lrt = get_last_review_ts(r["data"])
        due_dt = due_to_datetime(r["due"], r["type"], crt)
        cards.append(
            CardSummary(
                card_id=r["card_id"],
                question=question[:100],
                deck=deck_name_for_did(conn, r["did"]),
                tags=r["tags"],
                state=card_state_name(r["type"], lrt),
                due=due_dt.strftime("%Y-%m-%d %H:%M:%S"),
                suspended=r["queue"] == -1,
                buried=r["buried_until"] is not None and r["buried_until"] > _now_str(),
                lapse_count=r["lapses"],
            )
        )
    return CardListResult(cards=cards, total=total, limit=limit, offset=offset)


def get_card_detail(conn: sqlite3.Connection, card_id: int) -> CardDetail | None:
    """Get full card detail including FSRS state."""
    crt = _get_crt(conn)
    models = _get_models(conn)

    row = conn.execute(
        """
        SELECT c.id AS card_id, n.flds, n.mid, c.ord, n.tags,
               c.type, c.queue, c.due, c.ivl, c.factor, c.reps, c.lapses, c.data, c.did,
               sce.buried_until, sce.source,
               (SELECT COUNT(*) FROM revlog rl WHERE rl.cid = c.id) AS review_count
        FROM cards c
        JOIN notes n ON n.id = c.nid
        LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
        WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    if row is None:
        return None

    question, answer, extra = _render_from_row(row, models)
    fsrs_card = anki_fields_to_fsrs_card(dict(row), crt)
    retrievability = fsrs_engine.get_retrievability(fsrs_card)
    lrt = get_last_review_ts(row["data"])
    due_dt = due_to_datetime(row["due"], row["type"], crt)

    last_review_str: str | None = None
    if lrt is not None:
        last_review_str = datetime.fromtimestamp(lrt, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

    # cards.id is a ms epoch assigned at insert (see next_id()); immutable.
    created_ts = int(row["card_id"]) / 1000
    created_at = datetime.fromtimestamp(created_ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

    return CardDetail(
        card_id=row["card_id"],
        question=question,
        answer=answer,
        deck=deck_name_for_did(conn, row["did"]),
        tags=row["tags"],
        extra_fields=extra,
        source=row["source"] or "manual",
        suspended=row["queue"] == -1,
        buried_until=row["buried_until"],
        created_at=created_at,
        state=card_state_name(row["type"], lrt),
        due=due_dt.strftime("%Y-%m-%d %H:%M:%S"),
        stability=round(float(fsrs_card.stability or 0.0), 4),
        difficulty=round(float(fsrs_card.difficulty or 0.0), 4),
        retrievability=round(retrievability, 4),
        last_review=last_review_str,
        review_count=row["review_count"],
        lapse_count=row["lapses"],
    )


# ---------------------------------------------------------------------------
# Card mutations
# ---------------------------------------------------------------------------


def update_card(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    question: str | None = None,
    answer: str | None = None,
    tags: str | None = None,
    deck_id: int | None = None,
) -> bool:
    """Update card fields. Returns False if not found.

    Field resolution is template-aware: for multi-template notes (reversed
    pairs, custom imported note types) `question`/`answer` update the
    fields that correspond to this specific card's front/back, not fixed
    positions 0/1. For cloze cards this raises ClozeUpdateNotSupportedError —
    callers should use update_cloze_note instead.
    """
    now = int(time.time())
    row = conn.execute(
        "SELECT c.nid, c.ord, n.flds, n.mid "
        "FROM cards c JOIN notes n ON n.id = c.nid WHERE c.id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        return False

    nid = row["nid"]
    ord_val = int(row["ord"])
    mid_str = str(row["mid"])
    flds = str(row["flds"])
    parts = flds.split("\x1f")

    if question is not None or answer is not None:
        models = _get_models(conn)
        minfo = models.get(mid_str)
        if minfo is not None and minfo.model_type == 1:
            raise ClozeUpdateNotSupportedError(card_id)

        if minfo is None:
            qi, ai = 0, 1  # best-effort fallback for legacy rows
        else:
            qi_opt, ai_opt = resolve_card_qa_fields(minfo, ord_val)
            # model_type != 1 path returns concrete ints, but satisfy the checker
            qi = 0 if qi_opt is None else qi_opt
            ai = 1 if ai_opt is None else ai_opt

        if question is not None and qi < len(parts):
            parts[qi] = question
        if answer is not None and ai < len(parts):
            parts[ai] = answer
        new_flds = "\x1f".join(parts)
        sfld = parts[0] if parts else ""
        conn.execute(
            "UPDATE notes SET flds=?, sfld=?, mod=?, usn=-1 WHERE id=?",
            (new_flds, sfld, now, nid),
        )

    if tags is not None:
        conn.execute("UPDATE notes SET tags=?, mod=?, usn=-1 WHERE id=?", (tags, now, nid))

    if deck_id is not None:
        conn.execute("UPDATE cards SET did=?, mod=?, usn=-1 WHERE id=?", (deck_id, now, card_id))

    conn.execute("UPDATE cards SET mod=?, usn=-1 WHERE id=?", (now, card_id))
    invalidate_model_cache(conn)
    return True


def delete_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Delete a card and related data. Writes to graves. Returns False if not found."""
    row = conn.execute("SELECT nid FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        return False

    nid = row["nid"]

    # Delete review data (review_extra first — its subquery reads revlog)
    conn.execute(
        "DELETE FROM spacedrep_review_extra WHERE revlog_id IN "
        "(SELECT id FROM revlog WHERE cid = ?)",
        (card_id,),
    )
    conn.execute("DELETE FROM revlog WHERE cid = ?", (card_id,))
    conn.execute("DELETE FROM spacedrep_card_extra WHERE card_id = ?", (card_id,))

    # Write to graves
    conn.execute("INSERT INTO graves (usn, oid, type) VALUES (-1, ?, 0)", (card_id,))

    # Delete card
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))

    # Delete orphan note (no remaining cards)
    remaining = conn.execute("SELECT COUNT(*) AS cnt FROM cards WHERE nid = ?", (nid,)).fetchone()
    if remaining and remaining["cnt"] == 0:
        conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
        conn.execute("INSERT INTO graves (usn, oid, type) VALUES (-1, ?, 1)", (nid,))

    return True


def suspend_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Suspend a card (queue=-1). Returns False if not found."""
    now = int(time.time())
    cursor = conn.execute(
        "UPDATE cards SET queue = -1, mod = ?, usn = -1 WHERE id = ?", (now, card_id)
    )
    return cursor.rowcount > 0


def unsuspend_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Unsuspend a card (queue=type). Returns False if not found."""
    now = int(time.time())
    # Set queue to match type: 0=new, 1=learning, 2=review, 1=relearning
    row = conn.execute("SELECT type FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        return False
    card_type = row["type"]
    queue = {0: 0, 1: 1, 2: 2, 3: 1}.get(card_type, 0)
    conn.execute(
        "UPDATE cards SET queue = ?, mod = ?, usn = -1 WHERE id = ?", (queue, now, card_id)
    )
    return True


def bury_card(conn: sqlite3.Connection, card_id: int, until: str) -> bool:
    """Bury a card until a datetime. Returns False if not found."""
    existing = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existing is None:
        return False
    conn.execute(
        "INSERT INTO spacedrep_card_extra (card_id, buried_until) VALUES (?, ?)"
        " ON CONFLICT(card_id) DO UPDATE SET buried_until = excluded.buried_until",
        (card_id, until),
    )
    return True


def unbury_card(conn: sqlite3.Connection, card_id: int) -> bool:
    """Unbury a card. Returns False if not found."""
    existing = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existing is None:
        return False
    conn.execute(
        "UPDATE spacedrep_card_extra SET buried_until = NULL WHERE card_id = ?",
        (card_id,),
    )
    return True


def increment_lapse_count(conn: sqlite3.Connection, card_id: int) -> int:
    """Increment lapse count. Returns the new count."""
    conn.execute(
        "UPDATE cards SET lapses = lapses + 1, mod = ?, usn = -1 WHERE id = ?",
        (int(time.time()), card_id),
    )
    row = conn.execute("SELECT lapses FROM cards WHERE id = ?", (card_id,)).fetchone()
    return int(row["lapses"]) if row else 0


# ---------------------------------------------------------------------------
# FSRS state operations
# ---------------------------------------------------------------------------


def get_fsrs_card(conn: sqlite3.Connection, card_id: int) -> Card | None:
    """Reconstruct a py-fsrs Card from Anki card columns."""
    crt = _get_crt(conn)
    row = conn.execute(
        """SELECT c.type, c.due, c.ivl, c.factor, c.data,
                  sce.step
           FROM cards c
           LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
           WHERE c.id = ?""",
        (card_id,),
    ).fetchone()
    if row is None:
        return None
    return anki_fields_to_fsrs_card(dict(row), crt)


def update_fsrs_state(
    conn: sqlite3.Connection, card_id: int, fsrs_card: Card, retrievability: float
) -> None:
    """Write FSRS state back to Anki card columns."""
    crt = _get_crt(conn)
    fields = fsrs_card_to_anki_fields(fsrs_card, crt)

    # Preserve existing reps/lapses
    existing = conn.execute("SELECT reps, lapses FROM cards WHERE id = ?", (card_id,)).fetchone()
    if existing:
        fields["reps"] = existing["reps"] + 1
        fields["lapses"] = existing["lapses"]

    conn.execute(
        """UPDATE cards SET type=?, queue=?, due=?, ivl=?, factor=?,
           reps=?, lapses=?, left=?, odue=?, odid=?, flags=?,
           data=?, mod=?, usn=?
           WHERE id=?""",
        (
            fields["type"],
            fields["queue"],
            fields["due"],
            fields["ivl"],
            fields["factor"],
            fields["reps"],
            fields["lapses"],
            fields["left"],
            fields["odue"],
            fields["odid"],
            fields["flags"],
            fields["data"],
            fields["mod"],
            fields["usn"],
            card_id,
        ),
    )

    # Update step in extension table (always write, including 0, to clear stale values)
    step_val = fsrs_card.step if fsrs_card.step is not None else 0
    conn.execute(
        "INSERT INTO spacedrep_card_extra (card_id, step) VALUES (?, ?)"
        " ON CONFLICT(card_id) DO UPDATE SET step = excluded.step",
        (card_id, step_val),
    )


# ---------------------------------------------------------------------------
# Review operations
# ---------------------------------------------------------------------------


def insert_review_log(
    conn: sqlite3.Connection,
    review: ReviewInput,
    fsrs_log_json: str,
    *,
    updated_card: Card | None = None,
    previous_card: Card | None = None,
) -> None:
    """Insert a review into revlog + spacedrep_review_extra."""
    now_ms = next_id()

    # Compute ivl (new interval) from updated card's scheduled due date
    ivl = 0
    if updated_card and updated_card.due and updated_card.last_review:
        delta = updated_card.due - updated_card.last_review
        ivl = max(0, round(delta.total_seconds() / 86400))

    # Compute lastIvl (previous interval) from previous card state
    last_ivl = 0
    if previous_card and previous_card.due and previous_card.last_review:
        delta = previous_card.due - previous_card.last_review
        last_ivl = max(0, round(delta.total_seconds() / 86400))

    # Determine revlog type from card state before review: 0=learn, 1=review, 2=relearn
    revlog_type = 0
    if previous_card is not None:
        from fsrs import State

        if previous_card.state == State.Review:
            revlog_type = 1
        elif previous_card.state == State.Relearning:
            revlog_type = 2

    conn.execute(
        "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
        " VALUES (?, ?, -1, ?, ?, ?, 0, 0, ?)",
        (now_ms, review.card_id, review.rating, ivl, last_ivl, revlog_type),
    )

    # Extension data
    if review.user_answer or review.feedback or review.session_id:
        conn.execute(
            "INSERT INTO spacedrep_review_extra (revlog_id, user_answer, feedback, session_id)"
            " VALUES (?, ?, ?, ?)",
            (now_ms, review.user_answer, review.feedback, review.session_id),
        )


def get_review_history(conn: sqlite3.Connection, card_id: int) -> list[ReviewLogEntry]:
    """Get review history for a card."""
    rows = conn.execute(
        """SELECT r.id, r.cid, r.ease, r.id AS review_ts,
                  sre.user_answer, sre.feedback, sre.session_id
           FROM revlog r
           LEFT JOIN spacedrep_review_extra sre ON sre.revlog_id = r.id
           WHERE r.cid = ?
           ORDER BY r.id""",
        (card_id,),
    ).fetchall()
    return [
        ReviewLogEntry(
            card_id=row["cid"],
            rating=row["ease"],
            rating_name=fsrs_engine.rating_name(row["ease"]),
            reviewed_at=datetime.fromtimestamp(row["review_ts"] / 1000, tz=UTC).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            user_answer=row["user_answer"],
            feedback=row["feedback"],
            session_id=row["session_id"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Stats and config
# ---------------------------------------------------------------------------


def get_due_count(conn: sqlite3.Connection) -> DueCount:
    """Count due cards grouped by state."""
    crt = _get_crt(conn)
    now_ts = int(time.time())
    now_days = int((now_ts - crt) / 86400)

    rows = conn.execute(
        """SELECT c.type, c.data, COUNT(*) AS cnt
        FROM cards c
        LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
        WHERE c.queue >= 0
        AND (sce.buried_until IS NULL OR sce.buried_until <= ?)
        AND (
            (c.type = 0 AND c.queue = 0)
            OR (c.type IN (1, 3) AND c.due <= ?)
            OR (c.type = 2 AND c.due <= ?)
        )
        GROUP BY c.type""",
        (_now_str(), now_ts, now_days),
    ).fetchall()

    new = 0
    learning = 0
    review = 0
    for r in rows:
        card_type = r["type"]
        cnt = r["cnt"]
        if card_type == 0:
            new += cnt
        elif card_type == 1:
            learning += cnt
        elif card_type == 2:
            review += cnt
        elif card_type == 3:
            learning += cnt  # relearning counts as learning

    return DueCount(total_due=new + learning + review, learning=learning, review=review, new=new)


def get_next_due_time(conn: sqlite3.Connection) -> str | None:
    """Get the next due time for any non-suspended card after now."""
    crt = _get_crt(conn)
    now_ts = int(time.time())
    now_days = int((now_ts - crt) / 86400)

    # Check learning/relearning cards (due is unix timestamp)
    lr_row = conn.execute(
        "SELECT MIN(due) AS next_due FROM cards WHERE type IN (1, 3) AND queue >= 0 AND due > ?",
        (now_ts,),
    ).fetchone()

    # Check review cards (due is days since crt)
    rev_row = conn.execute(
        "SELECT MIN(due) AS next_due FROM cards WHERE type = 2 AND queue >= 0 AND due > ?",
        (now_days,),
    ).fetchone()

    candidates: list[datetime] = []
    if lr_row and lr_row["next_due"] is not None:
        candidates.append(datetime.fromtimestamp(lr_row["next_due"], tz=UTC))
    if rev_row and rev_row["next_due"] is not None:
        ts = crt + rev_row["next_due"] * 86400
        candidates.append(datetime.fromtimestamp(ts, tz=UTC))

    if not candidates:
        return None
    earliest = min(candidates)
    return earliest.strftime("%Y-%m-%d %H:%M:%S")


def get_session_stats(conn: sqlite3.Connection, session_id: str) -> SessionStats:
    """Get stats for a specific review session."""
    rows = conn.execute(
        """SELECT r.ease AS rating, COUNT(*) AS cnt
           FROM revlog r
           JOIN spacedrep_review_extra sre ON sre.revlog_id = r.id
           WHERE sre.session_id = ?
           GROUP BY r.ease""",
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
    crt = _get_crt(conn)
    now_ts = int(time.time())
    now_days = int((now_ts - crt) / 86400)

    total = conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
    total_cards = total["cnt"] if total else 0

    due_row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM cards c
        LEFT JOIN spacedrep_card_extra sce ON sce.card_id = c.id
        WHERE c.queue >= 0
        AND (sce.buried_until IS NULL OR sce.buried_until <= ?)
        AND (
            (c.type = 0 AND c.queue = 0)
            OR (c.type IN (1, 3) AND c.due <= ?)
            OR (c.type = 2 AND c.due <= ?)
        )""",
        (_now_str(), now_ts, now_days),
    ).fetchone()
    due_now = due_row["cnt"] if due_row else 0

    state_rows = conn.execute(
        """SELECT type, COUNT(*) AS cnt FROM cards
        WHERE queue >= 0
        GROUP BY type"""
    ).fetchall()

    learning = 0
    review = 0
    for r in state_rows:
        if r["type"] == 1:
            learning += r["cnt"]
        elif r["type"] == 2:
            review += r["cnt"]
        elif r["type"] == 3:
            learning += r["cnt"]

    # Mature: stability > 21 (from data.s)
    mature_row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM cards
        WHERE queue >= 0 AND data != '' AND json_extract(data, '$.s') > 21"""
    ).fetchone()
    mature = mature_row["cnt"] if mature_row else 0

    # Average retention: computed from FSRS cards with stability
    # For simplicity, compute from cards with data.s present
    fsrs_rows = conn.execute(
        "SELECT type, due, ivl, factor, data FROM cards WHERE queue >= 0 AND data != ''"
    ).fetchall()

    total_ret = 0.0
    ret_count = 0
    for r in fsrs_rows:
        try:
            fsrs_card = anki_fields_to_fsrs_card(dict(r), crt)
            if fsrs_card.last_review is not None:
                ret = fsrs_engine.get_retrievability(fsrs_card)
                total_ret += ret
                ret_count += 1
        except (ValueError, TypeError):
            continue

    avg_retention = total_ret / ret_count if ret_count > 0 else 0.0

    return OverallStats(
        total_cards=total_cards,
        due_now=due_now,
        learning=learning,
        review=review,
        mature=mature,
        avg_retention=round(avg_retention, 4),
    )


def get_config(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a config value by key from spacedrep_meta."""
    row = conn.execute("SELECT value FROM spacedrep_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a config value (upsert) in spacedrep_meta."""
    conn.execute(
        "INSERT INTO spacedrep_meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value),
    )


def get_all_review_log_jsons(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Get all review logs as (card_id, review_log_json) for optimizer.

    Constructs py-fsrs compatible ReviewLog JSON from revlog table.
    """
    rows = conn.execute(
        "SELECT cid, id, ease, ivl, lastIvl, time FROM revlog ORDER BY id"
    ).fetchall()
    results: list[tuple[int, str]] = []
    for r in rows:
        review_dt = datetime.fromtimestamp(r["id"] / 1000, tz=UTC)
        log = {
            "card_id": r["cid"],
            "rating": r["ease"],
            "review_datetime": review_dt.isoformat(),
            "review_duration": r["time"] if r["time"] else 0,
        }
        results.append((r["cid"], json.dumps(log)))
    return results


def get_review_logs_for_card(conn: sqlite3.Connection, card_id: int) -> list[str]:
    """Get review log JSONs for a card, ordered by review time."""
    rows = conn.execute(
        "SELECT id, ease, ivl, lastIvl, time FROM revlog WHERE cid = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    results: list[str] = []
    for r in rows:
        review_dt = datetime.fromtimestamp(r["id"] / 1000, tz=UTC)
        log = {
            "card_id": card_id,
            "rating": r["ease"],
            "review_datetime": review_dt.isoformat(),
            "review_duration": r["time"] if r["time"] else 0,
        }
        results.append(json.dumps(log))
    return results


# ---------------------------------------------------------------------------
# Tag operations
# ---------------------------------------------------------------------------


def list_tags(conn: sqlite3.Connection) -> list[str]:
    """Return all unique tags, sorted alphabetically."""
    rows = conn.execute("SELECT tags FROM notes WHERE tags != ''").fetchall()
    tag_set: set[str] = set()
    for row in rows:
        for tag in row["tags"].split():
            tag_set.add(tag)
    return sorted(tag_set)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def deck_name_for_did(conn: sqlite3.Connection, did: int) -> str:
    """Look up deck name from did via col.decks JSON."""
    meta = load_col_meta(conn)
    deck = meta.decks.get(str(did))
    if deck:
        return str(deck.get("name", "Unknown"))
    return "Default"


def _build_card_filter_clauses(
    *,
    deck: str | None = None,
    tags: list[str] | None = None,
    state: str | None = None,
    leech_threshold: int | None = None,
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
    crt: int = 0,
) -> tuple[str, list[Any]]:
    """Build SQL WHERE fragments for card filtering.

    Expects tables: c (cards), n (notes), sce (spacedrep_card_extra LEFT JOIN).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if deck is not None:
        # Need to resolve deck name to did(s) from col.decks
        # Use a subquery approach — match by deck name pattern
        clauses.append(
            "AND c.did IN (SELECT CAST(key AS INTEGER) FROM json_each("
            "(SELECT decks FROM col WHERE id=1), '$')"
            " WHERE json_extract(value, '$.name') = ?"
            " OR json_extract(value, '$.name') LIKE ?)"
        )
        params.extend([deck, f"{deck}::%"])

    if tags:
        tag_conditions: list[str] = []
        for tag in tags:
            tag_conditions.append(
                "((' ' || n.tags || ' ') LIKE ? OR (' ' || n.tags || ' ') LIKE ?)"
            )
            params.extend([f"% {tag} %", f"% {tag}::%"])
        clauses.append(f"AND ({' OR '.join(tag_conditions)})")

    if state is not None:
        if state == "new":
            clauses.append("AND c.type = 0")
        elif state == "learning":
            clauses.append("AND c.type = 1")
        elif state == "review":
            clauses.append("AND c.type = 2")
        elif state == "relearning":
            clauses.append("AND c.type = 3")

    if leech_threshold is not None:
        clauses.append("AND c.lapses >= ?")
        params.append(leech_threshold)

    if search is not None:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_param = f"%{escaped}%"
        clauses.append("AND n.flds LIKE ? ESCAPE '\\'")
        params.append(like_param)

    if suspended is True:
        clauses.append("AND c.queue = -1")
    elif suspended is False:
        clauses.append("AND c.queue >= 0")

    if due_before is not None:
        # Due before: need type-dependent check
        clauses.append(
            "AND ((c.type IN (1, 3) AND c.due <= ?) OR (c.type = 2 AND c.due <= ?) OR c.type = 0)"
        )
        try:
            dt = datetime.fromisoformat(due_before.replace("Z", "+00:00"))
            params.extend([int(dt.timestamp()), int((dt.timestamp() - crt) / 86400)])
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("due_before", due_before) from None

    if due_after is not None:
        clauses.append(
            "AND ((c.type IN (1, 3) AND c.due >= ?) OR (c.type = 2 AND c.due >= ?) OR c.type = 0)"
        )
        try:
            dt = datetime.fromisoformat(due_after.replace("Z", "+00:00"))
            params.extend([int(dt.timestamp()), int((dt.timestamp() - crt) / 86400)])
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("due_after", due_after) from None

    if created_before is not None:
        try:
            dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
            clauses.append("AND c.mod <= ?")
            params.append(int(dt.timestamp()))
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("created_before", created_before) from None

    if created_after is not None:
        try:
            dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
            clauses.append("AND c.mod >= ?")
            params.append(int(dt.timestamp()))
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("created_after", created_after) from None

    if reviewed_before is not None:
        try:
            dt = datetime.fromisoformat(reviewed_before.replace("Z", "+00:00"))
            clauses.append("AND c.data != '' AND json_extract(c.data, '$.lrt') <= ?")
            params.append(int(dt.timestamp()))
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("reviewed_before", reviewed_before) from None

    if reviewed_after is not None:
        try:
            dt = datetime.fromisoformat(reviewed_after.replace("Z", "+00:00"))
            clauses.append("AND c.data != '' AND json_extract(c.data, '$.lrt') >= ?")
            params.append(int(dt.timestamp()))
        except ValueError:
            from spacedrep.core import InvalidDateError

            raise InvalidDateError("reviewed_after", reviewed_after) from None

    # FSRS property expressions with fallbacks for unreviewed cards.
    # Matches display logic in anki_schema.anki_fields_to_fsrs_card():
    # - difficulty: from data JSON, else SM-2 factor estimate, else 5.0
    # - stability: from data JSON, else 0.0 (unreviewed)
    _diff_expr = (
        "(CASE WHEN c.data != '' AND json_extract(c.data, '$.d') IS NOT NULL"
        " THEN json_extract(c.data, '$.d')"
        " WHEN c.factor > 0"
        " THEN MAX(1.0, MIN(10.0, 10.0 - CAST(c.factor AS REAL) / 500.0))"
        " ELSE 5.0 END)"
    )
    _stab_expr = "COALESCE(CASE WHEN c.data != '' THEN json_extract(c.data, '$.s') END, 0.0)"

    if min_difficulty is not None:
        clauses.append(f"AND {_diff_expr} >= ?")
        params.append(min_difficulty)

    if max_difficulty is not None:
        clauses.append(f"AND {_diff_expr} <= ?")
        params.append(max_difficulty)

    if min_stability is not None:
        clauses.append(f"AND {_stab_expr} >= ?")
        params.append(min_stability)

    if max_stability is not None:
        clauses.append(f"AND {_stab_expr} <= ?")
        params.append(max_stability)

    # Retrievability filters: computed in Python (skip for now — too complex for SQL)
    # min/max_retrievability will be applied post-query if needed

    if buried is True:
        clauses.append("AND sce.buried_until IS NOT NULL AND sce.buried_until > ?")
        params.append(_now_str())
    elif buried is False:
        clauses.append("AND (sce.buried_until IS NULL OR sce.buried_until <= ?)")
        params.append(_now_str())

    return (" ".join(clauses), params)
