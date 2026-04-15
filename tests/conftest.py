"""Test fixtures."""

import json
import sqlite3
import tempfile
import zipfile
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import pytest

from spacedrep import core, db
from spacedrep.anki_schema import basic_guid


@pytest.fixture(autouse=True)
def _reset_fsrs_params() -> Generator[None, None, None]:  # pyright: ignore[reportUnusedFunction]
    """Reset FSRS parameters after each test to prevent state bleed."""
    yield
    core.reset_params_loaded()


@pytest.fixture(autouse=True)
def _clear_model_cache() -> Generator[None, None, None]:  # pyright: ignore[reportUnusedFunction]
    """Clear model cache after each test to prevent cross-test bleed."""
    yield
    db.clear_all_model_caches()


@pytest.fixture
def tmp_db() -> Generator[Path, None, None]:
    """Temporary database with Anki schema initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "collection.anki21"
        conn = db.get_connection(db_path)
        db.init_db(conn)
        conn.commit()
        conn.close()
        yield db_path


@pytest.fixture
def populated_db(tmp_db: Path) -> Path:
    """Database with sample cards in AWS deck."""
    conn = db.get_connection(tmp_db)
    db.upsert_deck(conn, "AWS")

    cards = [
        (
            "What is CAP theorem?",
            "In distributed systems, you can only guarantee two of three:"
            " consistency, availability, partition tolerance.",
            "distributed fundamentals",
        ),
        (
            "What is eventual consistency?",
            "A consistency model where replicas converge to the same value"
            " over time, but may return stale reads in the interim.",
            "distributed consistency",
        ),
        (
            "What is S3 storage class: Glacier?",
            "Low-cost archival storage with retrieval times from minutes to hours.",
            "aws s3 storage",
        ),
    ]
    for q, a, tags in cards:
        db.insert_card(
            conn,
            question=q,
            answer=a,
            deck_name="AWS",
            tags=tags,
            guid=basic_guid(q, "AWS"),
        )
    conn.commit()
    conn.close()
    return tmp_db


@pytest.fixture
def populated_db_multi_deck(tmp_db: Path) -> Path:
    """Database with cards in AWS and DSA decks with different tags."""
    conn = db.get_connection(tmp_db)

    aws_cards = [
        ("What is S3?", "Object storage", "s3 storage"),
        ("What is EC2?", "Virtual servers", "compute"),
        ("What is Lambda?", "Serverless compute", "compute serverless"),
    ]
    dsa_cards = [
        ("What is a binary tree?", "Tree with max 2 children", "trees"),
        ("What is BFS?", "Breadth-first search", "graphs search"),
    ]
    for q, a, tags in aws_cards:
        db.insert_card(
            conn,
            question=q,
            answer=a,
            deck_name="AWS",
            tags=tags,
            guid=basic_guid(q, "AWS"),
        )
    for q, a, tags in dsa_cards:
        db.insert_card(
            conn,
            question=q,
            answer=a,
            deck_name="DSA",
            tags=tags,
            guid=basic_guid(q, "DSA"),
        )
    conn.commit()
    conn.close()
    return tmp_db


# --- Synthetic .apkg fixture helpers ---


def build_anki_apkg(
    tmp_dir: Path,
    name: str,
    models: Mapping[str, Mapping[str, object]],
    decks: Mapping[str, Mapping[str, str]],
    notes: list[tuple[int, str, str, str, str]],
    cards: list[tuple[int, int, int, int, int]],
) -> Path:
    """Build a synthetic .apkg file with full Anki-format SQLite.

    Args:
        tmp_dir: Directory to write the .apkg into.
        name: Filename stem for the .apkg.
        models: Anki models JSON dict (keyed by model ID string).
        decks: Anki decks JSON dict (keyed by deck ID string).
        notes: List of (id, mid, flds, guid, tags) tuples.
        cards: List of (id, nid, did, ord, queue) tuples.

    Returns:
        Path to the created .apkg file.
    """
    apkg_path = tmp_dir / f"{name}.apkg"
    db_path = tmp_dir / f"{name}.anki21"

    conn = sqlite3.connect(str(db_path))

    # Full 13-column col table
    now = 1700000000
    conf: dict[str, Any] = {
        "curDeck": 1,
        "schedVer": 2,
        "nextPos": 1,
    }

    # Build full deck entries
    full_decks: dict[str, Any] = {}
    for did, deck_data in decks.items():
        full_decks[did] = {
            "id": int(did),
            "name": deck_data["name"],
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

    dconf: dict[str, Any] = {
        "1": {
            "id": 1,
            "name": "Default",
            "mod": 0,
            "usn": 0,
            "desiredRetention": 0.9,
        }
    }

    conn.execute(
        "CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER,"
        " scm INTEGER, ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER,"
        " conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute(
        "INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, -1, 0, ?, ?, ?, ?, '{}')",
        (
            now,
            now,
            now,
            json.dumps(conf),
            json.dumps(models),
            json.dumps(full_decks),
            json.dumps(dconf),
        ),
    )

    # Full notes table
    conn.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER,"
        " mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT,"
        " csum INTEGER, flags INTEGER, data TEXT)"
    )
    for nid, mid, flds, guid, tags_str in notes:
        sfld = flds.split("\x1f")[0] if flds else ""
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, ?, ?, ?, 0, 0, '')",
            (nid, guid, int(mid), now, tags_str, flds, sfld),
        )

    # Full cards table
    conn.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER,"
        " ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER,"
        " due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER,"
        " left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)"
    )
    for cid, nid, did, ord_val, queue in cards:
        card_type = 0 if queue >= 0 else 0
        actual_queue = queue
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, -1, ?, ?, 0, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, did, ord_val, now, card_type, actual_queue),
        )

    # Revlog table (empty)
    conn.execute(
        "CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER,"
        " ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER,"
        " time INTEGER, type INTEGER)"
    )

    # Graves table (empty)
    conn.execute("CREATE TABLE graves (usn INTEGER, oid INTEGER, type INTEGER)")

    conn.commit()
    conn.close()

    with zipfile.ZipFile(apkg_path, "w") as zf:
        zf.write(db_path, "collection.anki21")
        # Include empty media file for Anki compatibility
        zf.writestr("media", "{}")

    return apkg_path


BASIC_MODEL: dict[str, Any] = {
    "1000": {
        "id": 1000,
        "name": "Basic",
        "type": 0,
        "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
        "tmpls": [
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr>{{Back}}",
            }
        ],
    }
}

BASIC_REVERSED_MODEL: dict[str, Any] = {
    "2000": {
        "id": 2000,
        "name": "Basic (and reversed card)",
        "type": 0,
        "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
        "tmpls": [
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr>{{Back}}",
            },
            {
                "name": "Card 2",
                "qfmt": "{{Back}}",
                "afmt": "{{FrontSide}}<hr>{{Front}}",
            },
        ],
    }
}

CLOZE_MODEL: dict[str, Any] = {
    "3000": {
        "id": 3000,
        "name": "Cloze",
        "type": 1,
        "flds": [{"name": "Text", "ord": 0}, {"name": "Extra", "ord": 1}],
        "tmpls": [
            {
                "name": "Cloze",
                "qfmt": "{{cloze:Text}}",
                "afmt": "{{cloze:Text}}<br>{{Extra}}",
            }
        ],
    }
}

DEFAULT_DECK: dict[str, Any] = {"1": {"name": "Default"}}


@pytest.fixture(scope="session")
def cloze_apkg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic .apkg with a cloze note producing 2 cards."""
    tmp = tmp_path_factory.mktemp("apkg")
    return build_anki_apkg(
        tmp,
        "cloze",
        models=CLOZE_MODEL,
        decks=DEFAULT_DECK,
        notes=[
            (
                100,
                "3000",
                "{{c1::Ottawa}} is the capital of {{c2::Canada}}\x1fExtra info",
                "guid1",
                "geography",
            ),
        ],
        cards=[
            (1, 100, 1, 0, 0),  # c1 card, active
            (2, 100, 1, 1, 0),  # c2 card, active
        ],
    )


@pytest.fixture(scope="session")
def basic_reversed_apkg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic .apkg with a basic+reversed note producing 2 cards."""
    tmp = tmp_path_factory.mktemp("apkg")
    return build_anki_apkg(
        tmp,
        "reversed",
        models=BASIC_REVERSED_MODEL,
        decks=DEFAULT_DECK,
        notes=[
            (
                200,
                "2000",
                "What is Python?\x1fA programming language",
                "guid2",
                "programming",
            ),
        ],
        cards=[
            (3, 200, 1, 0, 0),  # forward card
            (4, 200, 1, 1, 0),  # reversed card
        ],
    )


@pytest.fixture(scope="session")
def suspended_apkg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic .apkg with one active and one suspended card."""
    tmp = tmp_path_factory.mktemp("apkg")
    return build_anki_apkg(
        tmp,
        "suspended",
        models=BASIC_MODEL,
        decks=DEFAULT_DECK,
        notes=[
            (300, "1000", "Active Q\x1fActive A", "guid3", ""),
            (301, "1000", "Suspended Q\x1fSuspended A", "guid4", ""),
        ],
        cards=[
            (5, 300, 1, 0, 0),  # active (queue=0)
            (6, 301, 1, 0, -1),  # suspended (queue=-1)
        ],
    )


@pytest.fixture(scope="session")
def mixed_apkg(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic .apkg combining cloze, basic+reversed, basic, and suspended cards."""
    tmp = tmp_path_factory.mktemp("apkg")
    models: dict[str, Any] = {**BASIC_MODEL, **BASIC_REVERSED_MODEL, **CLOZE_MODEL}
    decks: dict[str, Any] = {"1": {"name": "Default"}, "10": {"name": "Science"}}
    return build_anki_apkg(
        tmp,
        "mixed",
        models=models,
        decks=decks,
        notes=[
            # Cloze note -> 2 cards
            (400, "3000", "{{c1::Water}} is {{c2::H2O}}\x1f", "guidA", "chemistry"),
            # Basic+reversed note -> 2 cards
            (401, "2000", "Dog\x1fCanis familiaris", "guidB", "biology"),
            # Basic note -> 1 card, suspended
            (402, "1000", "Leech Q\x1fLeech A", "guidC", ""),
        ],
        cards=[
            (10, 400, 10, 0, 0),  # cloze c1, Science deck
            (11, 400, 10, 1, 0),  # cloze c2, Science deck
            (12, 401, 1, 0, 0),  # basic+reversed forward, Default deck
            (13, 401, 1, 1, 0),  # basic+reversed reversed, Default deck
            (14, 402, 1, 0, -1),  # basic, suspended
        ],
    )
