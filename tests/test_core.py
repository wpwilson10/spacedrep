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
        assert result["tables_created"] == 5


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


# --- list_tags tests ---


def test_list_tags(populated_db_multi_deck: Path) -> None:
    tags = core.list_tags(populated_db_multi_deck)
    assert "compute" in tags
    assert "s3" in tags
    assert "trees" in tags
    assert tags == sorted(tags)


def test_list_tags_empty(tmp_db: Path) -> None:
    tags = core.list_tags(tmp_db)
    assert tags == []


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


# --- Bulk Add tests ---


def test_add_cards_bulk(tmp_db: Path) -> None:
    from spacedrep.models import BulkCardInput

    cards = [BulkCardInput(question=f"Q{i}", answer=f"A{i}", deck="Test") for i in range(5)]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.count == 5
    assert len(result.created) == 5
    # IDs should be unique
    assert len(set(result.created)) == 5


def test_add_cards_bulk_empty(tmp_db: Path) -> None:
    result = core.add_cards_bulk(tmp_db, [])
    assert result.count == 0
    assert result.created == []


def test_add_cards_bulk_multi_deck(tmp_db: Path) -> None:
    from spacedrep.models import BulkCardInput

    cards = [
        BulkCardInput(question="Q1", answer="A1", deck="AWS"),
        BulkCardInput(question="Q2", answer="A2", deck="DSA"),
        BulkCardInput(question="Q3", answer="A3", deck="AWS"),
    ]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.count == 3
    # Verify decks were created
    decks = core.list_decks(tmp_db)
    deck_names = {d.name for d in decks}
    assert "AWS" in deck_names
    assert "DSA" in deck_names


# --- Leech Detection tests ---


def _review_card_to_review_state(db_path: Path, card_id: int) -> None:
    """Review a card with 'good' to move it to Review state."""
    # First review moves from Learning, subsequent reviews keep it in Review
    for _ in range(3):
        review = ReviewInput(card_id=card_id, rating=3)  # good
        core.submit_review(db_path, review)


def test_lapse_increments_on_again_review(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")
    _review_card_to_review_state(tmp_db, 1)

    # Verify card is in review state
    detail = core.get_card_detail(tmp_db, 1)
    assert detail.state == "review"

    # Rate "again" — should increment lapse count
    review = ReviewInput(card_id=1, rating=1)
    core.submit_review(tmp_db, review)

    detail = core.get_card_detail(tmp_db, 1)
    assert detail.lapse_count == 1


def test_no_lapse_on_learning_again(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")

    # Card is in "new" (Learning) state — again should NOT increment lapse
    review = ReviewInput(card_id=1, rating=1)
    core.submit_review(tmp_db, review)

    detail = core.get_card_detail(tmp_db, 1)
    assert detail.lapse_count == 0


def test_leech_auto_suspends(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")
    _review_card_to_review_state(tmp_db, 1)

    # Rate "again" 8 times to trigger leech
    is_leech_result = False
    for _ in range(8):
        # After each "again", the card goes to Relearning. Review it to Review first.
        detail = core.get_card_detail(tmp_db, 1)
        if detail.state in ("relearning", "learning"):
            review = ReviewInput(card_id=1, rating=3)
            core.submit_review(tmp_db, review)
        review = ReviewInput(card_id=1, rating=1)
        result = core.submit_review(tmp_db, review)
        if result.is_leech:
            is_leech_result = True

    assert is_leech_result
    detail = core.get_card_detail(tmp_db, 1)
    assert detail.suspended
    assert detail.lapse_count >= 8


def test_lapse_count_in_card_detail(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")
    detail = core.get_card_detail(tmp_db, 1)
    assert detail.lapse_count == 0


def test_list_leeches_filter(tmp_db: Path) -> None:
    # Add cards, no leeches yet
    core.add_card(tmp_db, "Q1", "A1", deck="Test")
    core.add_card(tmp_db, "Q2", "A2", deck="Test")

    result = core.list_cards(tmp_db, leeches_only=True)
    assert result.total == 0


# --- Review Preview tests ---


def test_preview_review(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")
    preview = core.preview_review(tmp_db, 1)
    assert preview.card_id == 1
    assert preview.current_state == "new"
    assert len(preview.previews) == 4
    assert "again" in preview.previews
    assert "hard" in preview.previews
    assert "good" in preview.previews
    assert "easy" in preview.previews

    # Each preview should have different intervals
    intervals = [p.interval_days for p in preview.previews.values()]
    assert len(set(intervals)) > 1  # at least 2 different intervals


def test_preview_reviewed_card(tmp_db: Path) -> None:
    core.add_card(tmp_db, "Q", "A", deck="Test")
    review = ReviewInput(card_id=1, rating=3)
    core.submit_review(tmp_db, review)

    preview = core.preview_review(tmp_db, 1)
    assert preview.current_state in ("learning", "review")
    for name, p in preview.previews.items():
        assert p.stability > 0
        assert p.rating == name


def test_preview_review_not_found(tmp_db: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.preview_review(tmp_db, 9999)


# --- FSRS Status tests ---


def test_get_fsrs_status_default(tmp_db: Path) -> None:
    status = core.get_fsrs_status(tmp_db)
    assert status.is_default
    assert status.review_count == 0
    assert status.min_reviews_needed == 512
    assert not status.can_optimize


def test_optimize_insufficient_reviews(tmp_db: Path) -> None:
    """With very few reviews, optimization runs but can't meaningfully improve params."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    review = ReviewInput(card_id=1, rating=3)
    core.submit_review(tmp_db, review)

    result = core.optimize_parameters(tmp_db)
    assert result.review_count == 1


def test_params_loaded_from_config(tmp_db: Path) -> None:
    import json

    from spacedrep import db as _db
    from spacedrep import fsrs_engine

    # Store custom params in config
    conn = _db.get_connection(tmp_db)
    _db.migrate_db(conn)
    custom_params = list(fsrs_engine.DEFAULT_PARAMS)
    custom_params[0] = 0.1234  # Modify first param
    _db.set_config(conn, "fsrs_parameters", json.dumps(custom_params))
    conn.commit()
    conn.close()

    # Reset and re-open — should load custom params
    core.reset_params_loaded()
    status = core.get_fsrs_status(tmp_db)
    assert not status.is_default
    assert abs(status.parameters[0] - 0.1234) < 0.001


# --- Anki compatibility import tests ---


def test_import_cloze_produces_two_cards(tmp_db: Path, cloze_apkg: Path) -> None:
    result = core.import_deck(tmp_db, cloze_apkg)
    assert result.imported == 2
    assert result.updated == 0
    cards = core.list_cards(tmp_db).cards
    questions = {c.question[:20] for c in cards}
    assert any("[...]" in q for q in questions)


def test_import_cloze_content(tmp_db: Path, cloze_apkg: Path) -> None:
    core.import_deck(tmp_db, cloze_apkg)
    cards = core.list_cards(tmp_db).cards
    details = [core.get_card_detail(tmp_db, c.card_id) for c in cards]
    # Sort by card_id to get stable c1, c2 order
    details.sort(key=lambda d: d.card_id)
    # c1 (ord=0): Ottawa blanked, Canada shown
    assert "[...]" in details[0].question and "Canada" in details[0].question
    # c2 (ord=1): Ottawa shown, Canada blanked
    assert "Ottawa" in details[1].question and "[...]" in details[1].question


def test_import_basic_reversed_produces_two_cards(tmp_db: Path, basic_reversed_apkg: Path) -> None:
    result = core.import_deck(tmp_db, basic_reversed_apkg)
    assert result.imported == 2
    cards = core.list_cards(tmp_db).cards
    questions = {c.question for c in cards}
    assert "What is Python?" in questions
    assert "A programming language" in questions


def test_import_suspended_cards(tmp_db: Path, suspended_apkg: Path) -> None:
    result = core.import_deck(tmp_db, suspended_apkg)
    assert result.imported == 2
    cards = core.list_cards(tmp_db).cards
    suspended = [c for c in cards if c.suspended]
    active = [c for c in cards if not c.suspended]
    assert len(suspended) == 1
    assert len(active) == 1
    assert suspended[0].question == "Suspended Q"


def test_import_mixed_apkg(tmp_db: Path, mixed_apkg: Path) -> None:
    result = core.import_deck(tmp_db, mixed_apkg)
    assert result.imported == 5  # 2 cloze + 2 reversed + 1 basic
    cards = core.list_cards(tmp_db).cards
    suspended = [c for c in cards if c.suspended]
    assert len(suspended) == 1
    assert suspended[0].question == "Leech Q"


def test_import_dedup_composite_key(tmp_db: Path, cloze_apkg: Path) -> None:
    first = core.import_deck(tmp_db, cloze_apkg)
    assert first.imported == 2
    second = core.import_deck(tmp_db, cloze_apkg)
    assert second.updated == 2
    assert second.imported == 0
    # Still only 2 cards total
    assert core.list_cards(tmp_db).total == 2


def test_import_reimport_updates_suspended(
    tmp_db: Path, suspended_apkg: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Re-import with changed suspension status updates the card."""
    from tests.conftest import BASIC_MODEL, DEFAULT_DECK, build_anki_apkg

    core.import_deck(tmp_db, suspended_apkg)
    cards = core.list_cards(tmp_db).cards
    assert any(c.suspended for c in cards)

    # Build a modified apkg where the suspended card is now active
    tmp = tmp_path_factory.mktemp("reimport")
    unsuspended_apkg = build_anki_apkg(
        tmp,
        "unsuspended",
        models=BASIC_MODEL,
        decks=DEFAULT_DECK,
        notes=[
            (300, "1000", "Active Q\x1fActive A", "guid3", ""),
            (301, "1000", "Suspended Q\x1fSuspended A", "guid4", ""),
        ],
        cards=[
            (5, 300, 1, 0, 0),  # still active
            (6, 301, 1, 0, 0),  # was suspended, now active (queue=0)
        ],
    )
    result = core.import_deck(tmp_db, unsuspended_apkg)
    assert result.updated == 2

    cards = core.list_cards(tmp_db).cards
    assert not any(c.suspended for c in cards)


def test_import_dry_run_multi_card(tmp_db: Path, mixed_apkg: Path) -> None:
    result = core.import_deck(tmp_db, mixed_apkg, dry_run=True)
    assert result.dry_run is True
    assert result.imported == 5
    assert result.updated == 0
    # No cards actually written
    assert core.list_cards(tmp_db).total == 0
