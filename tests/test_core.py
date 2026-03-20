"""Tests for core business logic."""

import tempfile
from pathlib import Path

import pytest

from spacedrep import core
from spacedrep.models import ReviewInput


def test_init_database() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        result = core.init_database(db_path)
        assert result["status"] == "ok"
        assert result["tables_created"] == 4


def test_add_card_and_get_next(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "What is X?", "X is Y.", deck="Test")
    assert "card_id" in result
    assert result["deck"] == "Test"

    due = core.get_next_card(tmp_db)
    assert due is not None
    assert due.question == "What is X?"
    assert due.state == "new"


def test_submit_review(populated_db: Path) -> None:
    due = core.get_next_card(populated_db)
    assert due is not None

    review = ReviewInput(card_id=due.card_id, rating=3)
    result = core.submit_review(populated_db, review)
    assert result.rating == "good"
    assert result.stability > 0


def test_invalid_rating(populated_db: Path) -> None:
    due = core.get_next_card(populated_db)
    assert due is not None

    review = ReviewInput(card_id=due.card_id, rating=5)
    with pytest.raises(core.InvalidRatingError):
        core.submit_review(populated_db, review)


def test_card_not_found(tmp_db: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.suspend_card(tmp_db, 9999)


def test_database_not_found() -> None:
    with pytest.raises(core.DatabaseNotFoundError):
        core.get_next_card(Path("/nonexistent/db.sqlite"))


def test_suspend_unsuspend(populated_db: Path) -> None:
    due = core.get_next_card(populated_db)
    assert due is not None

    core.suspend_card(populated_db, due.card_id)

    # Card should no longer appear as due
    counts = core.get_due_count(populated_db)
    assert counts.total_due == 2  # 3 - 1 suspended

    core.unsuspend_card(populated_db, due.card_id)
    counts = core.get_due_count(populated_db)
    assert counts.total_due == 3


def test_due_count(populated_db: Path) -> None:
    counts = core.get_due_count(populated_db)
    assert counts.total_due == 3
    assert counts.new == 3


def test_list_decks(populated_db: Path) -> None:
    decks = core.list_decks(populated_db)
    assert len(decks) >= 1
    assert any(d.name == "AWS" for d in decks)


def test_overall_stats(populated_db: Path) -> None:
    stats = core.get_overall_stats(populated_db)
    assert stats.total_cards == 3


def test_session_stats(populated_db: Path) -> None:
    # Review some cards with a session ID
    due = core.get_next_card(populated_db)
    assert due is not None
    review = ReviewInput(card_id=due.card_id, rating=3, session_id="test-session")
    core.submit_review(populated_db, review)

    stats = core.get_session_stats(populated_db, "test-session")
    assert stats.reviewed == 1
    assert stats.good == 1
    assert stats.accuracy == 1.0


def test_full_flow() -> None:
    """End-to-end: init → add → next → review → stats."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        core.init_database(db_path)

        core.add_card(db_path, "Q1", "A1", deck="Test", tags="tag1")
        core.add_card(db_path, "Q2", "A2", deck="Test", tags="tag2")

        counts = core.get_due_count(db_path)
        assert counts.total_due == 2

        card = core.get_next_card(db_path)
        assert card is not None

        review = ReviewInput(card_id=card.card_id, rating=3, session_id="s1")
        result = core.submit_review(db_path, review)
        assert result.rating == "good"

        stats = core.get_session_stats(db_path, "s1")
        assert stats.reviewed == 1

        overall = core.get_overall_stats(db_path)
        assert overall.total_cards == 2


# --- get_next_card filter tests ---


def test_get_next_card_by_deck(populated_db_multi_deck: Path) -> None:
    result = core.get_next_card(populated_db_multi_deck, deck="DSA")
    assert result is not None
    assert result.deck == "DSA"


def test_get_next_card_by_tags(populated_db_multi_deck: Path) -> None:
    result = core.get_next_card(populated_db_multi_deck, tags=["trees"])
    assert result is not None
    assert "trees" in result.tags


def test_get_next_card_by_state(populated_db_multi_deck: Path) -> None:
    result = core.get_next_card(populated_db_multi_deck, state="new")
    assert result is not None
    assert result.state == "new"


def test_get_next_card_invalid_state(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.InvalidStateError):
        core.get_next_card(populated_db_multi_deck, state="invalid")


# --- list_cards tests ---


def test_list_cards_basic(populated_db_multi_deck: Path) -> None:
    result = core.list_cards(populated_db_multi_deck)
    assert result.total == 5
    assert len(result.cards) == 5
    assert result.limit == 50
    assert result.offset == 0


def test_list_cards_pagination(populated_db_multi_deck: Path) -> None:
    result = core.list_cards(populated_db_multi_deck, limit=2, offset=0)
    assert len(result.cards) == 2
    assert result.total == 5


# --- get_card_detail tests ---


def test_get_card_detail_found(populated_db_multi_deck: Path) -> None:
    detail = core.get_card_detail(populated_db_multi_deck, 1)
    assert detail.card_id == 1
    assert detail.question == "What is S3?"
    assert detail.state == "new"


def test_get_card_detail_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.get_card_detail(populated_db_multi_deck, 9999)


# --- delete_card tests ---


def test_delete_card_success(populated_db_multi_deck: Path) -> None:
    result = core.delete_card(populated_db_multi_deck, 1)
    assert result["card_id"] == 1
    assert result["deleted"] is True


def test_delete_card_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.delete_card(populated_db_multi_deck, 9999)


# --- update_card tests ---


def test_update_card_question(populated_db_multi_deck: Path) -> None:
    detail = core.update_card(populated_db_multi_deck, 1, question="Updated Q")
    assert detail.question == "Updated Q"
    assert detail.answer == "Object storage"  # unchanged


def test_update_card_deck_change(populated_db_multi_deck: Path) -> None:
    detail = core.update_card(populated_db_multi_deck, 1, deck="DSA")
    assert detail.deck == "DSA"


def test_update_card_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.update_card(populated_db_multi_deck, 9999, question="nope")
