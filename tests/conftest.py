"""Test fixtures."""

import json
import sqlite3
import tempfile
import zipfile
from collections.abc import Generator, Mapping
from pathlib import Path

import pytest

from spacedrep import core, db
from spacedrep.models import CardRecord


@pytest.fixture(autouse=True)
def _reset_fsrs_params() -> Generator[None, None, None]:  # pyright: ignore[reportUnusedFunction]
    """Reset FSRS parameters after each test to prevent state bleed."""
    yield
    core.reset_params_loaded()


@pytest.fixture
def tmp_db() -> Generator[Path, None, None]:
    """Temporary database with schema initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = db.get_connection(db_path)
        db.init_db(conn)
        conn.commit()
        conn.close()
        yield db_path


@pytest.fixture
def sample_cards() -> list[CardRecord]:
    """Sample card records for testing."""
    return [
        CardRecord(
            deck_id=1,
            question="What is CAP theorem?",
            answer=(
                "In distributed systems, you can only guarantee two of three:"
                " consistency, availability, partition tolerance."
            ),
            tags="distributed fundamentals",
            source="manual",
        ),
        CardRecord(
            deck_id=1,
            question="What is eventual consistency?",
            answer=(
                "A consistency model where replicas converge to the same value"
                " over time, but may return stale reads in the interim."
            ),
            tags="distributed consistency",
            source="manual",
        ),
        CardRecord(
            deck_id=1,
            question="What is S3 storage class: Glacier?",
            answer="Low-cost archival storage with retrieval times from minutes to hours.",
            tags="aws s3 storage",
            source="manual",
        ),
    ]


@pytest.fixture
def populated_db(tmp_db: Path, sample_cards: list[CardRecord]) -> Path:
    """Database with sample cards and FSRS state."""
    conn = db.get_connection(tmp_db)
    db.upsert_deck(conn, "AWS")
    for card in sample_cards:
        db.insert_card(conn, card)
    conn.commit()
    conn.close()
    return tmp_db


@pytest.fixture
def populated_db_multi_deck(tmp_db: Path) -> Path:
    """Database with cards in AWS and DSA decks with different tags."""
    conn = db.get_connection(tmp_db)
    aws_id = db.upsert_deck(conn, "AWS")
    dsa_id = db.upsert_deck(conn, "DSA")

    aws_cards = [
        CardRecord(
            deck_id=aws_id,
            question="What is S3?",
            answer="Object storage",
            tags="s3 storage",
        ),
        CardRecord(
            deck_id=aws_id,
            question="What is EC2?",
            answer="Virtual servers",
            tags="compute",
        ),
        CardRecord(
            deck_id=aws_id,
            question="What is Lambda?",
            answer="Serverless compute",
            tags="compute serverless",
        ),
    ]
    dsa_cards = [
        CardRecord(
            deck_id=dsa_id,
            question="What is a binary tree?",
            answer="Tree with max 2 children",
            tags="trees",
        ),
        CardRecord(
            deck_id=dsa_id,
            question="What is BFS?",
            answer="Breadth-first search",
            tags="graphs search",
        ),
    ]
    for card in aws_cards + dsa_cards:
        db.insert_card(conn, card)
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
    """Build a synthetic .apkg file with Anki-format SQLite.

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
    conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, models TEXT, decks TEXT)")
    conn.execute(
        "INSERT INTO col VALUES (1, ?, ?)",
        (json.dumps(models), json.dumps(decks)),
    )
    conn.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT, guid TEXT, tags TEXT)"
    )
    conn.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, "
        "ord INTEGER, queue INTEGER)"
    )
    conn.executemany("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", notes)
    conn.executemany("INSERT INTO cards VALUES (?, ?, ?, ?, ?)", cards)
    conn.commit()
    conn.close()

    with zipfile.ZipFile(apkg_path, "w") as zf:
        zf.write(db_path, "collection.anki21")

    return apkg_path


BASIC_MODEL = {
    "1000": {
        "id": 1000,
        "name": "Basic",
        "type": 0,
        "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
        "tmpls": [{"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}"}],
    }
}

BASIC_REVERSED_MODEL = {
    "2000": {
        "id": 2000,
        "name": "Basic (and reversed card)",
        "type": 0,
        "flds": [{"name": "Front", "ord": 0}, {"name": "Back", "ord": 1}],
        "tmpls": [
            {"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}"},
            {"name": "Card 2", "qfmt": "{{Back}}", "afmt": "{{FrontSide}}<hr>{{Front}}"},
        ],
    }
}

CLOZE_MODEL = {
    "3000": {
        "id": 3000,
        "name": "Cloze",
        "type": 1,
        "flds": [{"name": "Text", "ord": 0}, {"name": "Extra", "ord": 1}],
        "tmpls": [
            {"name": "Cloze", "qfmt": "{{cloze:Text}}", "afmt": "{{cloze:Text}}<br>{{Extra}}"}
        ],
    }
}

DEFAULT_DECK = {"1": {"name": "Default"}}


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
            (200, "2000", "What is Python?\x1fA programming language", "guid2", "programming"),
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
    models = {**BASIC_MODEL, **BASIC_REVERSED_MODEL, **CLOZE_MODEL}
    decks = {"1": {"name": "Default"}, "10": {"name": "Science"}}
    return build_anki_apkg(
        tmp,
        "mixed",
        models=models,
        decks=decks,
        notes=[
            # Cloze note → 2 cards
            (400, "3000", "{{c1::Water}} is {{c2::H2O}}\x1f", "guidA", "chemistry"),
            # Basic+reversed note → 2 cards
            (401, "2000", "Dog\x1fCanis familiaris", "guidB", "biology"),
            # Basic note → 1 card, suspended
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
