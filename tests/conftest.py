"""Test fixtures."""

import tempfile
from collections.abc import Generator
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
            tags="distributed,fundamentals",
            source="manual",
        ),
        CardRecord(
            deck_id=1,
            question="What is eventual consistency?",
            answer=(
                "A consistency model where replicas converge to the same value"
                " over time, but may return stale reads in the interim."
            ),
            tags="distributed,consistency",
            source="manual",
        ),
        CardRecord(
            deck_id=1,
            question="What is S3 storage class: Glacier?",
            answer="Low-cost archival storage with retrieval times from minutes to hours.",
            tags="aws,s3,storage",
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
            tags="s3,storage",
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
            tags="compute,serverless",
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
            tags="graphs,search",
        ),
    ]
    for card in aws_cards + dsa_cards:
        db.insert_card(conn, card)
    conn.commit()
    conn.close()
    return tmp_db
