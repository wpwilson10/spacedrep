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
    assert table_names == {"decks", "cards", "fsrs_state", "review_logs", "config"}
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
    card_id, was_update = db.insert_card(conn, card)
    assert not was_update
    conn.commit()

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.question == "What is X?"
    assert detail.answer == "X is Y."
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
    id1, was_update1 = db.insert_card(conn, card1)
    assert not was_update1

    card2 = CardRecord(
        deck_id=deck_id,
        question="Updated Q",
        answer="Updated A",
        source="apkg",
        source_note_id=12345,
    )
    id2, was_update2 = db.insert_card(conn, card2)
    assert was_update2
    conn.commit()

    assert id1 == id2
    detail = db.get_card_detail(conn, id1)
    assert detail is not None
    assert detail.question == "Updated Q"
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

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.suspended

    assert db.unsuspend_card(conn, card_id)
    conn.commit()

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert not detail.suspended
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


# --- Filter tests for get_next_due_card ---


def test_get_next_due_card_by_deck(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    due = db.get_next_due_card(conn, deck="DSA")
    assert due is not None
    assert due.deck == "DSA"
    conn.close()


def test_get_next_due_card_by_tags(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    due = db.get_next_due_card(conn, tags=["s3"])
    assert due is not None
    assert "s3" in due.tags
    conn.close()


def test_get_next_due_card_by_state(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    due = db.get_next_due_card(conn, state="new")
    assert due is not None
    assert due.state == "new"

    # No cards in "review" state yet
    due_review = db.get_next_due_card(conn, state="review")
    assert due_review is None
    conn.close()


# --- list_cards tests ---


def test_list_cards_no_filter(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn)
    assert result.total == 5
    assert len(result.cards) == 5
    conn.close()


def test_list_cards_pagination(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    page1 = db.list_cards(conn, limit=2, offset=0)
    assert len(page1.cards) == 2
    assert page1.total == 5

    page2 = db.list_cards(conn, limit=2, offset=2)
    assert len(page2.cards) == 2
    assert page2.cards[0].card_id != page1.cards[0].card_id
    conn.close()


def test_list_cards_by_deck(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn, deck="AWS")
    assert result.total == 3
    assert all(c.deck == "AWS" for c in result.cards)
    conn.close()


def test_list_cards_by_tags(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn, tags=["compute"])
    assert result.total == 2
    assert all("compute" in c.tags for c in result.cards)
    conn.close()


def test_list_cards_by_state(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn, state="new")
    assert result.total == 5  # all cards are new
    conn.close()


# --- get_card_detail tests ---


def test_get_card_detail_found(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    detail = db.get_card_detail(conn, 1)
    assert detail is not None
    assert detail.card_id == 1
    assert detail.question == "What is S3?"
    assert detail.deck == "AWS"
    assert detail.state == "new"
    assert detail.review_count == 0
    conn.close()


def test_get_card_detail_not_found(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    detail = db.get_card_detail(conn, 9999)
    assert detail is None
    conn.close()


# --- delete_card tests ---


def test_delete_card_success(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    assert db.delete_card(conn, 1)
    conn.commit()

    # Card and FSRS state should be gone
    assert db.get_card_detail(conn, 1) is None
    fsrs = conn.execute("SELECT 1 FROM fsrs_state WHERE card_id = 1").fetchone()
    assert fsrs is None
    conn.close()


def test_delete_card_not_found(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    assert not db.delete_card(conn, 9999)
    conn.close()


# --- update_card tests ---


def test_update_card_question(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    assert db.update_card(conn, 1, question="Updated S3 question")
    conn.commit()

    detail = db.get_card_detail(conn, 1)
    assert detail is not None
    assert detail.question == "Updated S3 question"
    # answer unchanged
    assert detail.answer == "Object storage"
    conn.close()


def test_update_card_deck(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    dsa_id = conn.execute("SELECT id FROM decks WHERE name = 'DSA'").fetchone()["id"]
    assert db.update_card(conn, 1, deck_id=dsa_id)
    conn.commit()

    detail = db.get_card_detail(conn, 1)
    assert detail is not None
    assert detail.deck == "DSA"
    conn.close()


def test_update_card_not_found(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    assert not db.update_card(conn, 9999, question="nope")
    conn.close()


# --- Leech / lapse_count tests ---


def test_increment_lapse_count(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    count = db.increment_lapse_count(conn, 1)
    assert count == 1
    count = db.increment_lapse_count(conn, 1)
    assert count == 2
    conn.close()


def test_lapse_count_in_card_detail(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    detail = db.get_card_detail(conn, 1)
    assert detail is not None
    assert detail.lapse_count == 0

    db.increment_lapse_count(conn, 1)
    detail = db.get_card_detail(conn, 1)
    assert detail is not None
    assert detail.lapse_count == 1
    conn.close()


def test_lapse_count_in_list_cards(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    db.increment_lapse_count(conn, 1)
    result = db.list_cards(conn)
    card = next(c for c in result.cards if c.card_id == 1)
    assert card.lapse_count == 1
    conn.close()


def test_list_cards_leech_filter(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    # No leeches yet
    result = db.list_cards(conn, leech_threshold=8)
    assert result.total == 0

    # Push card 1 lapse count to 8
    for _ in range(8):
        db.increment_lapse_count(conn, 1)
    result = db.list_cards(conn, leech_threshold=8)
    assert result.total == 1
    assert result.cards[0].card_id == 1
    conn.close()


# --- Config tests ---


def test_config_get_set(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    assert db.get_config(conn, "nonexistent") is None

    db.set_config(conn, "test_key", "test_value")
    assert db.get_config(conn, "test_key") == "test_value"

    # Upsert
    db.set_config(conn, "test_key", "updated")
    assert db.get_config(conn, "test_key") == "updated"
    conn.close()


# --- Review log query tests ---


def test_get_all_review_log_jsons_empty(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    result = db.get_all_review_log_jsons(conn)
    assert result == []
    conn.close()


def test_get_review_logs_for_card_empty(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    result = db.get_review_logs_for_card(conn, 999)
    assert result == []
    conn.close()
