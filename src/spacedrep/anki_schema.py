"""Anki SQLite schema definitions and conversion helpers.

Provides DDL for the Anki database tables, ColMeta for managing the collection
metadata row, FSRS<->Anki field conversion, due date encoding/decoding, and
deterministic GUID generation.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fsrs import Card, State

# Anki's JSON blobs (col.models, col.decks, col.conf, col.dconf) have deeply
# nested, externally-defined structure. Using Any for these is intentional —
# the schema is defined by Anki, not by us.
AnkiJson = dict[str, Any]


def _json_or_empty(value: Any, default: Any) -> Any:
    """Parse JSON if value is truthy, else return default.

    Anki 2.1.49+ leaves col.* JSON columns as empty strings (data lives
    in split tables). Treat empty as default so ColMeta.from_row stays
    safe; the higher layer raises a clear error when appropriate.
    """
    if not value:
        return default
    return json.loads(str(value))


# ---------------------------------------------------------------------------
# Model and deck ID constants (stable hashes matching apkg_writer.py)
# ---------------------------------------------------------------------------

BASIC_MODEL_ID = int(hashlib.sha256(b"spacedrep").hexdigest()[:8], 16)
CLOZE_MODEL_ID = int(hashlib.sha256(b"spacedrep-cloze").hexdigest()[:8], 16)
BASIC_REVERSED_MODEL_ID = int(hashlib.sha256(b"spacedrep-reversed").hexdigest()[:8], 16)


def _deck_id_from_name(name: str) -> int:
    """Generate a stable deck ID from the deck name."""
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Default model definitions
# ---------------------------------------------------------------------------

_BASIC_MODEL: AnkiJson = {
    "id": BASIC_MODEL_ID,
    "name": "spacedrep",
    "type": 0,
    "sortf": 0,
    "did": 1,
    "usn": -1,
    "flds": [
        {
            "name": "Question",
            "ord": 0,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
        {
            "name": "Answer",
            "ord": 1,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
    ],
    "tmpls": [
        {
            "name": "Card 1",
            "ord": 0,
            "qfmt": "{{Question}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Answer}}',
            "did": None,
        },
    ],
    "css": (
        ".card { font-family: arial; font-size: 20px;"
        " text-align: center; color: black; background-color: white; }"
    ),
    "vers": [],
    "mod": 0,
}

_BASIC_REVERSED_MODEL: AnkiJson = {
    "id": BASIC_REVERSED_MODEL_ID,
    "name": "spacedrep-reversed",
    "type": 0,
    "sortf": 0,
    "did": 1,
    "usn": -1,
    "flds": [
        {
            "name": "Question",
            "ord": 0,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
        {
            "name": "Answer",
            "ord": 1,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
    ],
    "tmpls": [
        {
            "name": "Card 1",
            "ord": 0,
            "qfmt": "{{Question}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Answer}}',
            "did": None,
        },
        {
            "name": "Card 2",
            "ord": 1,
            "qfmt": "{{Answer}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Question}}',
            "did": None,
        },
    ],
    "css": (
        ".card { font-family: arial; font-size: 20px;"
        " text-align: center; color: black; background-color: white; }"
    ),
    "vers": [],
    "mod": 0,
}

_CLOZE_MODEL: AnkiJson = {
    "id": CLOZE_MODEL_ID,
    "name": "spacedrep-cloze",
    "type": 1,
    "sortf": 0,
    "did": 1,
    "usn": -1,
    "flds": [
        {
            "name": "Text",
            "ord": 0,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
        {
            "name": "Back Extra",
            "ord": 1,
            "sticky": False,
            "rtl": False,
            "font": "Arial",
            "size": 20,
            "media": [],
        },
    ],
    "tmpls": [
        {
            "name": "Cloze",
            "ord": 0,
            "qfmt": "{{cloze:Text}}",
            "afmt": "{{cloze:Text}}<br>{{Back Extra}}",
            "did": None,
        },
    ],
    "css": (
        ".card { font-family: arial; font-size: 20px;"
        " text-align: center; color: black; background-color: white; }"
    ),
    "vers": [],
    "mod": 0,
}

# ---------------------------------------------------------------------------
# Default collection config (matches genanki's proven defaults)
# ---------------------------------------------------------------------------

_DEFAULT_CONF: AnkiJson = {
    "curDeck": 1,
    "curModel": str(BASIC_MODEL_ID),
    "nextPos": 1,
    "schedVer": 2,
    "activeDecks": [1],
    "newSpread": 0,
    "collapseTime": 1200,
    "timeLim": 0,
    "estTimes": True,
    "dueCounts": True,
    "dayLearnFirst": False,
    "sortType": "noteFld",
    "sortBackwards": False,
}

_DEFAULT_DCONF: dict[str, AnkiJson] = {
    "1": {
        "id": 1,
        "name": "Default",
        "mod": 0,
        "usn": 0,
        "maxTaken": 60,
        "autoplay": True,
        "timer": 0,
        "replayq": True,
        "new": {
            "bury": True,
            "delays": [1, 10],
            "initialFactor": 2500,
            "ints": [1, 4, 7],
            "order": 1,
            "perDay": 20,
        },
        "rev": {
            "bury": True,
            "ease4": 1.3,
            "fuzz": 0.05,
            "ivlFct": 1,
            "maxIvl": 36500,
            "perDay": 200,
        },
        "lapse": {
            "delays": [10],
            "leechAction": 0,
            "leechFails": 8,
            "minInt": 1,
            "mult": 0,
        },
        "desiredRetention": 0.9,
        "fsrsWeights": [],
    }
}


# ---------------------------------------------------------------------------
# Anki DDL
# ---------------------------------------------------------------------------

ANKI_SCHEMA = """\
CREATE TABLE IF NOT EXISTS col (
    id       INTEGER PRIMARY KEY,
    crt      INTEGER NOT NULL,
    mod      INTEGER NOT NULL,
    scm      INTEGER NOT NULL,
    ver      INTEGER NOT NULL,
    dty      INTEGER NOT NULL,
    usn      INTEGER NOT NULL,
    ls       INTEGER NOT NULL,
    conf     TEXT NOT NULL,
    models   TEXT NOT NULL,
    decks    TEXT NOT NULL,
    dconf    TEXT NOT NULL,
    tags     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id    INTEGER PRIMARY KEY,
    guid  TEXT NOT NULL,
    mid   INTEGER NOT NULL,
    mod   INTEGER NOT NULL,
    usn   INTEGER NOT NULL,
    tags  TEXT NOT NULL,
    flds  TEXT NOT NULL,
    sfld  TEXT NOT NULL,
    csum  INTEGER NOT NULL,
    flags INTEGER NOT NULL,
    data  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id     INTEGER PRIMARY KEY,
    nid    INTEGER NOT NULL,
    did    INTEGER NOT NULL,
    ord    INTEGER NOT NULL,
    mod    INTEGER NOT NULL,
    usn    INTEGER NOT NULL,
    type   INTEGER NOT NULL,
    queue  INTEGER NOT NULL,
    due    INTEGER NOT NULL,
    ivl    INTEGER NOT NULL,
    factor INTEGER NOT NULL,
    reps   INTEGER NOT NULL,
    lapses INTEGER NOT NULL,
    left   INTEGER NOT NULL,
    odue   INTEGER NOT NULL,
    odid   INTEGER NOT NULL,
    flags  INTEGER NOT NULL,
    data   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revlog (
    id      INTEGER PRIMARY KEY,
    cid     INTEGER NOT NULL,
    usn     INTEGER NOT NULL,
    ease    INTEGER NOT NULL,
    ivl     INTEGER NOT NULL,
    lastIvl INTEGER NOT NULL,
    factor  INTEGER NOT NULL,
    time    INTEGER NOT NULL,
    type    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS graves (
    usn  INTEGER NOT NULL,
    oid  INTEGER NOT NULL,
    type INTEGER NOT NULL
);

-- Spacedrep extension tables (Anki ignores unknown tables)

CREATE TABLE IF NOT EXISTS spacedrep_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spacedrep_card_extra (
    card_id      INTEGER PRIMARY KEY,
    step         INTEGER,
    buried_until TEXT,
    source       TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS spacedrep_review_extra (
    revlog_id   INTEGER PRIMARY KEY,
    user_answer TEXT,
    feedback    TEXT,
    session_id  TEXT
);

-- Indexes

CREATE INDEX IF NOT EXISTS idx_notes_guid ON notes (guid);
CREATE INDEX IF NOT EXISTS idx_cards_nid  ON cards (nid);
CREATE INDEX IF NOT EXISTS idx_cards_did  ON cards (did);
CREATE INDEX IF NOT EXISTS idx_revlog_cid ON revlog (cid);
"""


# ---------------------------------------------------------------------------
# ColMeta — parsed col row
# ---------------------------------------------------------------------------


@dataclass
class ColMeta:
    """Parsed collection metadata from the col table."""

    crt: int  # creation timestamp (seconds)
    mod: int
    scm: int
    ver: int
    conf: AnkiJson
    models: dict[str, AnkiJson]
    decks: dict[str, AnkiJson]
    dconf: dict[str, AnkiJson]
    tags: AnkiJson = field(default_factory=lambda: {})

    @classmethod
    def default(cls) -> "ColMeta":
        """Create default collection metadata for a fresh database."""
        now = int(time.time())
        default_deck = {
            "1": {
                "id": 1,
                "name": "Default",
                "mod": now,
                "usn": -1,
                "collapsed": False,
                "dyn": 0,
                "desc": "",
                "conf": 1,
                "extendNew": 0,
                "extendRev": 50,
                "lrnToday": [0, 0],
                "newToday": [0, 0],
                "revToday": [0, 0],
                "timeToday": [0, 0],
            }
        }
        return cls(
            crt=now,
            mod=now,
            scm=now,
            ver=11,
            conf=_DEFAULT_CONF,
            models={
                str(BASIC_MODEL_ID): _BASIC_MODEL,
                str(CLOZE_MODEL_ID): _CLOZE_MODEL,
                str(BASIC_REVERSED_MODEL_ID): _BASIC_REVERSED_MODEL,
            },
            decks=default_deck,
            dconf=_DEFAULT_DCONF,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ColMeta":
        """Parse a col table row into ColMeta.

        Empty JSON strings are tolerated and map to empty dicts — some
        Anki 2.1.49+ DBs leave the legacy col.* JSON columns empty and
        move the data to separate tables. Detection of that case and
        raising a clear error is done at a higher layer (db._is_modern_anki_schema
        + core._open_db); this method just doesn't crash.
        """
        return cls(
            crt=int(row["crt"]),
            mod=int(row["mod"]),
            scm=int(row["scm"]),
            ver=int(row["ver"]),
            conf=_json_or_empty(row.get("conf"), {}),
            models=_json_or_empty(row.get("models"), {}),
            decks=_json_or_empty(row.get("decks"), {}),
            dconf=_json_or_empty(row.get("dconf"), {}),
            tags=_json_or_empty(row.get("tags"), {}),
        )

    def to_col_row(self) -> dict[str, Any]:
        """Serialize to a dict suitable for INSERT into col table."""
        return {
            "id": 1,
            "crt": self.crt,
            "mod": self.mod,
            "scm": self.scm,
            "ver": self.ver,
            "dty": 0,
            "usn": -1,
            "ls": 0,
            "conf": json.dumps(self.conf),
            "models": json.dumps(self.models),
            "decks": json.dumps(self.decks),
            "dconf": json.dumps(self.dconf),
            "tags": json.dumps(self.tags),
        }

    def get_deck_id(self, name: str) -> int | None:
        """Look up a deck ID by name. Returns None if not found."""
        for did, deck in self.decks.items():
            if deck.get("name") == name:
                return int(did)
        return None

    def ensure_deck(self, name: str) -> int:
        """Get or create a deck by name. Returns the deck ID."""
        existing = self.get_deck_id(name)
        if existing is not None:
            return existing

        deck_id = _deck_id_from_name(name)
        now = int(time.time())
        self.decks[str(deck_id)] = {
            "id": deck_id,
            "name": name,
            "mod": now,
            "usn": -1,
            "collapsed": False,
            "dyn": 0,
            "desc": "",
            "conf": 1,
            "extendNew": 0,
            "extendRev": 50,
            "lrnToday": [0, 0],
            "newToday": [0, 0],
            "revToday": [0, 0],
            "timeToday": [0, 0],
        }
        self.mod = now
        return deck_id

    def ensure_model(self, model_type: str) -> int:
        """Get the model ID for 'basic', 'cloze', or 'reversed'. Adds model if missing.

        Args:
            model_type: "basic", "cloze", or "reversed".

        Returns:
            The model ID.

        Raises:
            ValueError: if model_type is not one of the supported values.
        """
        if model_type == "basic":
            mid, template = BASIC_MODEL_ID, _BASIC_MODEL
        elif model_type == "cloze":
            mid, template = CLOZE_MODEL_ID, _CLOZE_MODEL
        elif model_type == "reversed":
            mid, template = BASIC_REVERSED_MODEL_ID, _BASIC_REVERSED_MODEL
        else:
            msg = f"Unknown model_type: {model_type!r}. Must be 'basic', 'cloze', or 'reversed'."
            raise ValueError(msg)

        mid_str = str(mid)
        if mid_str not in self.models:
            self.models[mid_str] = template
            self.mod = int(time.time())

        return mid


# ---------------------------------------------------------------------------
# Due date helpers
# ---------------------------------------------------------------------------


def due_to_datetime(due: int, card_type: int, crt: int) -> datetime:
    """Convert Anki's due field to a datetime.

    Args:
        due: The cards.due value.
        card_type: The cards.type value (0=new, 1=learning, 2=review, 3=relearning).
        crt: Collection creation timestamp (col.crt, seconds).

    Returns:
        A timezone-aware datetime in UTC.
    """
    if card_type == 0:
        # New card: due is a position integer, not a date.
        # Return epoch as a sentinel — new cards don't have a meaningful due date.
        return datetime.fromtimestamp(0, tz=UTC)
    if card_type in (1, 3):
        # Learning/relearning: due is a unix timestamp in seconds
        return datetime.fromtimestamp(due, tz=UTC)
    # Review (type=2): due is days since col.crt
    return datetime.fromtimestamp(crt + due * 86400, tz=UTC)


def datetime_to_due(dt: datetime, card_type: int, crt: int) -> int:
    """Convert a datetime to Anki's due field value.

    Args:
        dt: The target due datetime.
        card_type: The cards.type value (0=new, 1=learning, 2=review, 3=relearning).
        crt: Collection creation timestamp (col.crt, seconds).

    Returns:
        Integer due value appropriate for the card type.
    """
    if card_type == 0:
        # New card: due is a position integer. Caller should set this directly.
        return 0
    if card_type in (1, 3):
        # Learning/relearning: due is a unix timestamp in seconds
        return int(dt.timestamp())
    # Review (type=2): due is days since col.crt
    return int((dt.timestamp() - crt) / 86400)


# ---------------------------------------------------------------------------
# FSRS <-> Anki conversion
# ---------------------------------------------------------------------------

# Anki card.type -> FSRS State mapping
_ANKI_TYPE_TO_STATE: dict[int, State] = {
    0: State.Learning,  # new -> treat as learning
    1: State.Learning,
    2: State.Review,
    3: State.Relearning,
}

# FSRS State -> Anki card.type mapping
_STATE_TO_ANKI_TYPE: dict[State, int] = {
    State.Learning: 1,
    State.Review: 2,
    State.Relearning: 3,
}

# FSRS State -> Anki card.queue mapping
_STATE_TO_ANKI_QUEUE: dict[State, int] = {
    State.Learning: 1,
    State.Review: 2,
    State.Relearning: 1,
}


def fsrs_card_to_anki_fields(fsrs_card: Card, crt: int) -> dict[str, Any]:
    """Convert a py-fsrs Card to Anki card column values.

    Returns a dict with all scheduling-related card columns.
    """
    state = fsrs_card.state
    now = int(time.time())

    # Determine card type
    if state == State.Learning and fsrs_card.last_review is None:
        # New card (never reviewed)
        card_type = 0
        queue = 0
        due = 0  # position; caller should set appropriately
    else:
        card_type = _STATE_TO_ANKI_TYPE.get(state, 1)
        queue = _STATE_TO_ANKI_QUEUE.get(state, 1)
        due = datetime_to_due(fsrs_card.due, card_type, crt)

    # Stability and difficulty
    stability = fsrs_card.stability
    difficulty = fsrs_card.difficulty
    ivl = round(stability) if stability is not None else 0

    # Build data JSON
    data: dict[str, Any] = {}
    if stability is not None:
        data["s"] = round(stability, 4)
    if difficulty is not None:
        data["d"] = round(difficulty, 4)
    if fsrs_card.last_review is not None:
        data["lrt"] = int(fsrs_card.last_review.timestamp())

    # Count reps from card (py-fsrs doesn't track reps directly; we rely on
    # the caller passing the current reps count via the card or separately)
    # For now, we don't have reps in fsrs Card — caller must merge.

    return {
        "type": card_type,
        "queue": queue,
        "due": due,
        "ivl": ivl,
        "factor": 2500,
        "reps": 0,  # caller should merge from existing card
        "lapses": 0,  # caller should merge from existing card
        "left": 0,
        "odue": 0,
        "odid": 0,
        "flags": 0,
        "data": json.dumps(data) if data else "",
        "mod": now,
        "usn": -1,
    }


def anki_fields_to_fsrs_card(
    row: dict[str, Any],
    crt: int,
) -> Card:
    """Reconstruct a py-fsrs Card from Anki card column values.

    For SM-2 cards (no data.s/d), estimates FSRS values:
    - stability ~ ivl
    - difficulty ~ 10 - (factor / 500)
    """
    card_type = int(row["type"])
    due_val = int(row["due"])
    ivl = int(row["ivl"])
    factor = int(row["factor"])

    # Parse data JSON
    data_str = str(row.get("data", "") or "")
    data: dict[str, Any] = json.loads(data_str) if data_str else {}

    # State
    state = _ANKI_TYPE_TO_STATE.get(card_type, State.Learning)

    # Stability and difficulty
    if "s" in data:
        stability = float(data["s"])
    elif ivl > 0:
        # SM-2 estimation: stability ~ interval
        stability = float(ivl)
    else:
        stability = None

    if "d" in data:
        difficulty = float(data["d"])
    elif factor > 0:
        # SM-2 estimation: difficulty ~ 10 - (factor / 500)
        difficulty = max(1.0, min(10.0, 10.0 - factor / 500.0))
    else:
        difficulty = None

    # Last review
    last_review: datetime | None = None
    if "lrt" in data:
        last_review = datetime.fromtimestamp(int(data["lrt"]), tz=UTC)
    elif card_type == 2 and ivl > 0:
        # Review card: last_review ~ due - ivl (in days since crt)
        review_ts = crt + (due_val - ivl) * 86400
        if review_ts > 0:
            last_review = datetime.fromtimestamp(review_ts, tz=UTC)

    # Due date
    due_dt = datetime.now(tz=UTC) if card_type == 0 else due_to_datetime(due_val, card_type, crt)

    # Step
    step = 0
    if "step" in row:
        step = int(row["step"] or 0)

    card = Card()
    card.state = state
    card.step = step
    card.stability = stability
    card.difficulty = difficulty
    card.due = due_dt
    card.last_review = last_review

    return card


# ---------------------------------------------------------------------------
# GUID generation
# ---------------------------------------------------------------------------


def basic_guid(question: str, deck_name: str) -> str:
    """Generate a deterministic GUID for a basic card.

    Returns a 10-char hex string for Anki's notes.guid field.
    """
    return hashlib.sha256(f"{question}\x1f{deck_name}".encode()).hexdigest()[:10]


def cloze_guid(cloze_text: str) -> str:
    """Generate a deterministic GUID for a cloze note.

    Returns a 10-char hex string for Anki's notes.guid field.
    """
    return hashlib.sha256(cloze_text.encode()).hexdigest()[:10]


def reversed_guid(question: str, deck_name: str) -> str:
    """Generate a deterministic GUID for a reversed note.

    Namespaced with a "reversed" prefix so it cannot collide with basic_guid
    for the same (question, deck) pair — the two live in separate GUID spaces
    by note type. Dedup is on (question, deck), so re-adding with a changed
    answer updates the existing note and preserves review history.

    Returns a 10-char hex string for Anki's notes.guid field.
    """
    return hashlib.sha256(f"reversed\x1f{question}\x1f{deck_name}".encode()).hexdigest()[:10]
