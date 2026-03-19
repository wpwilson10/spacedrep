"""Tests for db module."""

from pathlib import Path

from spacedrep import db
from spacedrep.models import CardRecord


def test_init_db(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    assert table_names == {"decks", "cards", "fsrs_state", "review_logs"}
    conn.close()


def test_upsert_deck(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    id1 = db.upsert_deck(conn, "AWS")
    id2 = db.upsert_deck(conn, "AWS")
    assert id1 == id2
    id3 = db.upsert_deck(conn, "DSA")
    assert id3 != id1
    conn.close()


def test_insert_and_get_card(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    deck_id = db.upsert_deck(conn, "Test")
    card = CardRecord(
        deck_id=deck_id,
        question="What is X?",
        answer="X is Y.",
        tags="test",
    )
    card_id = db.insert_card(conn, card)
    conn.commit()

    retrieved = db.get_card(conn, card_id)
    assert retrieved is not None
    assert retrieved.question == "What is X?"
    assert retrieved.answer == "X is Y."
    conn.close()


def test_card_dedup_on_source_note_id(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    deck_id = db.upsert_deck(conn, "Test")

    card1 = CardRecord(
        deck_id=deck_id,
        question="Original Q",
        answer="Original A",
        source="apkg",
        source_note_id=12345,
    )
    id1 = db.insert_card(conn, card1)

    card2 = CardRecord(
        deck_id=deck_id,
        question="Updated Q",
        answer="Updated A",
        source="apkg",
        source_note_id=12345,
    )
    id2 = db.insert_card(conn, card2)
    conn.commit()

    assert id1 == id2
    updated = db.get_card(conn, id1)
    assert updated is not None
    assert updated.question == "Updated Q"
    conn.close()


def test_get_next_due_card(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    due = db.get_next_due_card(conn)
    assert due is not None
    assert due.card_id > 0
    assert due.question
    conn.close()


def test_suspend_unsuspend(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)

    due = db.get_next_due_card(conn)
    assert due is not None
    card_id = due.card_id

    assert db.suspend_card(conn, card_id)
    conn.commit()

    card = db.get_card(conn, card_id)
    assert card is not None
    assert card.suspended

    assert db.unsuspend_card(conn, card_id)
    conn.commit()

    card = db.get_card(conn, card_id)
    assert card is not None
    assert not card.suspended
    conn.close()


def test_due_count(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    counts = db.get_due_count(conn)
    assert counts.total_due == 3
    assert counts.new == 3
    conn.close()


def test_get_all_cards(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    cards = db.get_all_cards(conn)
    assert len(cards) == 3
    conn.close()


def test_overall_stats(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    stats = db.get_overall_stats(conn)
    assert stats.total_cards == 3
    assert stats.due_now == 3
    conn.close()
