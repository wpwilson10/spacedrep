"""Tests for core business logic."""

import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from spacedrep import core
from spacedrep.models import ReviewInput


def _write_modern_schema_db(path: Path) -> None:
    """Fabricate a SQLite file shaped like Anki 2.1.49+ (empty col JSON
    columns + populated notetypes table)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER,"
        " scm INTEGER, ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER,"
        " conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '', '', '', '', '')")
    conn.execute("CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO notetypes VALUES (1, 'Basic')")
    conn.commit()
    conn.close()


def _write_modern_schema_apkg(apkg: Path, inner_db: Path) -> None:
    """Zip a modern-schema SQLite file into an .apkg."""
    _write_modern_schema_db(inner_db)
    with zipfile.ZipFile(apkg, "w") as zf:
        zf.write(inner_db, "collection.anki21")
        zf.writestr("media", "{}")


def test_init_database() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        result = core.init_database(db_path)
        assert result["status"] == "ok"
        assert result["tables_created"] == 8


def test_add_card_and_get_next(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "What is X?", "X is Y.", deck="Test")
    assert "card_id" in result
    assert result["deck"] == "Test"

    due = core.get_next_card(tmp_db)
    assert due is not None
    assert due.question == "What is X?"
    assert due.state == "new"


def test_add_card_dedup_same_deck(tmp_db: Path) -> None:
    """Adding the same question+deck twice returns same card_id with was_update=True."""
    r1 = core.add_card(tmp_db, "What is X?", "X is Y.", deck="Test")
    r2 = core.add_card(tmp_db, "What is X?", "Updated answer", deck="Test")
    assert r1["card_id"] == r2["card_id"]
    assert r2["was_update"] is True

    # Verify the answer was updated
    detail = core.get_card_detail(tmp_db, int(r1["card_id"]))
    assert detail.answer == "Updated answer"


def test_add_card_dedup_preserves_tags(tmp_db: Path) -> None:
    """Re-adding a card without specifying tags preserves existing tags."""
    r1 = core.add_card(tmp_db, "What is X?", "Answer", deck="Test", tags="t1 t2")
    detail1 = core.get_card_detail(tmp_db, int(r1["card_id"]))
    assert detail1.tags == "t1 t2"

    # Re-add without tags — existing tags should be preserved
    r2 = core.add_card(tmp_db, "What is X?", "Updated answer", deck="Test")
    assert r2["was_update"] is True
    detail2 = core.get_card_detail(tmp_db, int(r2["card_id"]))
    assert detail2.tags == "t1 t2"
    assert detail2.answer == "Updated answer"

    # Re-add with explicit new tags — tags should update
    r3 = core.add_card(tmp_db, "What is X?", "Updated answer", deck="Test", tags="t3")
    detail3 = core.get_card_detail(tmp_db, int(r3["card_id"]))
    assert detail3.tags == "t3"


def test_add_card_dedup_different_deck(tmp_db: Path) -> None:
    """Same question in different decks creates separate cards."""
    r1 = core.add_card(tmp_db, "What is X?", "A1", deck="DeckA")
    r2 = core.add_card(tmp_db, "What is X?", "A2", deck="DeckB")
    assert r1["card_id"] != r2["card_id"]


def test_add_cards_bulk_dedup(tmp_db: Path) -> None:
    """Bulk add with duplicate entries dedupes returned ids and total.

    Contract: `total == len(created) == number of distinct cards that
    exist as a result of this call`. A re-add of (question, deck)
    updates the existing card in place, so it contributes a single id
    to `created`, not one id per input row.
    """
    from spacedrep.models import BulkCardInput

    cards = [
        BulkCardInput(question="Q1", answer="A1", deck="Test"),
        BulkCardInput(question="Q1", answer="A1-updated", deck="Test"),
        BulkCardInput(question="Q2", answer="A2", deck="Test"),
    ]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.total == 2
    assert len(result.created) == 2
    assert result.created[0] != result.created[1]
    # The re-add updated the first card in place.
    d = core.get_card_detail(tmp_db, result.created[0])
    assert d is not None
    assert d.answer == "A1-updated"


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
    """End-to-end: init -> add -> next -> review -> stats."""
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
    cards = core.list_cards(populated_db_multi_deck)
    card_id = cards.cards[0].card_id
    detail = core.get_card_detail(populated_db_multi_deck, card_id)
    assert detail.card_id == card_id
    assert detail.question == "What is S3?"
    assert detail.state == "new"


def test_get_card_detail_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.get_card_detail(populated_db_multi_deck, 9999)


# --- delete_card tests ---


def test_delete_card_success(populated_db_multi_deck: Path) -> None:
    cards = core.list_cards(populated_db_multi_deck)
    card_id = cards.cards[0].card_id
    result = core.delete_card(populated_db_multi_deck, card_id)
    assert result["card_id"] == card_id
    assert result["deleted"] is True


def test_delete_card_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.delete_card(populated_db_multi_deck, 9999)


# --- update_card tests ---


def test_update_card_question(populated_db_multi_deck: Path) -> None:
    cards = core.list_cards(populated_db_multi_deck)
    card_id = cards.cards[0].card_id
    detail = core.update_card(populated_db_multi_deck, card_id, question="Updated Q")
    assert detail.question == "Updated Q"
    assert detail.answer == "Object storage"  # unchanged


def test_update_card_deck_change(populated_db_multi_deck: Path) -> None:
    cards = core.list_cards(populated_db_multi_deck)
    card_id = cards.cards[0].card_id
    detail = core.update_card(populated_db_multi_deck, card_id, deck="DSA")
    assert detail.deck == "DSA"


def test_update_card_not_found(populated_db_multi_deck: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.update_card(populated_db_multi_deck, 9999, question="nope")


def test_update_card_deck_move_cloze(tmp_db: Path) -> None:
    """Moving a cloze sibling moves all siblings together (issue #7A)."""
    cloze = core.add_cloze_note(
        tmp_db, "{{c1::Ottawa}} is the capital of {{c2::Canada}}", deck="Geo"
    )
    assert cloze.card_count == 2
    c1, c2 = cloze.card_ids[0], cloze.card_ids[1]

    core.update_card(tmp_db, c1, deck="Other")

    d1 = core.get_card_detail(tmp_db, c1)
    d2 = core.get_card_detail(tmp_db, c2)
    assert d1.deck == "Other"
    assert d2.deck == "Other"


def test_update_card_deck_move_reversed(tmp_db: Path) -> None:
    """Moving one card of a reversed pair moves its sibling too (regression)."""
    rev = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
    assert rev.card_count == 2
    c0, c1 = rev.card_ids[0], rev.card_ids[1]

    core.update_card(tmp_db, c0, deck="other")

    d0 = core.get_card_detail(tmp_db, c0)
    d1 = core.get_card_detail(tmp_db, c1)
    assert d0.deck == "other"
    assert d1.deck == "other"


def test_update_card_deck_move_basic_unaffected(tmp_db: Path) -> None:
    """Moving a basic card does not affect unrelated basic cards."""
    a = core.add_card(tmp_db, "Qa", "Aa", deck="DeckA")
    b = core.add_card(tmp_db, "Qb", "Ab", deck="DeckA")
    a_id, b_id = int(a["card_id"]), int(b["card_id"])

    core.update_card(tmp_db, a_id, deck="DeckB")

    da = core.get_card_detail(tmp_db, a_id)
    db_detail = core.get_card_detail(tmp_db, b_id)
    assert da.deck == "DeckB"
    assert db_detail.deck == "DeckA"


# --- Bulk Add tests ---


def test_add_cards_bulk(tmp_db: Path) -> None:
    from spacedrep.models import BulkCardInput

    cards = [BulkCardInput(question=f"Q{i}", answer=f"A{i}", deck="Test") for i in range(5)]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.total == 5
    assert len(result.created) == 5
    # IDs should be unique
    assert len(set(result.created)) == 5


def test_add_cards_bulk_empty(tmp_db: Path) -> None:
    result = core.add_cards_bulk(tmp_db, [])
    assert result.total == 0
    assert result.created == []


def test_add_cards_bulk_multi_deck(tmp_db: Path) -> None:
    from spacedrep.models import BulkCardInput

    cards = [
        BulkCardInput(question="Q1", answer="A1", deck="AWS"),
        BulkCardInput(question="Q2", answer="A2", deck="DSA"),
        BulkCardInput(question="Q3", answer="A3", deck="AWS"),
    ]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.total == 3
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
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    _review_card_to_review_state(tmp_db, card_id)

    # Verify card is in review state
    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.state == "review"

    # Rate "again" -- should increment lapse count
    review = ReviewInput(card_id=card_id, rating=1)
    core.submit_review(tmp_db, review)

    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.lapse_count == 1


def test_no_lapse_on_learning_again(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])

    # Card is in "new" (Learning) state -- again should NOT increment lapse
    review = ReviewInput(card_id=card_id, rating=1)
    core.submit_review(tmp_db, review)

    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.lapse_count == 0


def test_leech_auto_suspends(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    _review_card_to_review_state(tmp_db, card_id)

    # Rate "again" 8 times to trigger leech
    is_leech_result = False
    for _ in range(8):
        # After each "again", the card goes to Relearning. Review it to Review first.
        detail = core.get_card_detail(tmp_db, card_id)
        if detail.state in ("relearning", "learning"):
            review = ReviewInput(card_id=card_id, rating=3)
            core.submit_review(tmp_db, review)
        review = ReviewInput(card_id=card_id, rating=1)
        result_review = core.submit_review(tmp_db, review)
        if result_review.is_leech:
            is_leech_result = True

    assert is_leech_result
    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.lapse_count >= 8
    assert detail.suspended is True


def test_lapse_count_in_card_detail(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.lapse_count == 0


def test_list_leeches_filter(tmp_db: Path) -> None:
    # Add cards, no leeches yet
    core.add_card(tmp_db, "Q1", "A1", deck="Test")
    core.add_card(tmp_db, "Q2", "A2", deck="Test")

    result = core.list_cards(tmp_db, leeches_only=True)
    assert result.total == 0


# --- Review Preview tests ---


def test_preview_review(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    preview = core.preview_review(tmp_db, card_id)
    assert preview.card_id == card_id
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
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    review = ReviewInput(card_id=card_id, rating=3)
    core.submit_review(tmp_db, review)

    preview = core.preview_review(tmp_db, card_id)
    assert preview.current_state in ("learning", "review")
    for name, p in preview.previews.items():
        assert p.stability > 0
        assert p.rating == name


def test_preview_review_not_found(tmp_db: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.preview_review(tmp_db, 9999)


# --- Review History tests ---


def test_get_review_history(tmp_db: Path) -> None:
    result = core.add_card(tmp_db, "Q", "A")
    card_id = int(result["card_id"])
    core.submit_review(tmp_db, ReviewInput(card_id=card_id, rating=3))
    core.submit_review(tmp_db, ReviewInput(card_id=card_id, rating=4))
    history = core.get_review_history(tmp_db, card_id)
    assert history.card_id == card_id
    assert history.total == 2
    assert history.reviews[0].rating_name == "good"
    assert history.reviews[1].rating_name == "easy"


def test_get_review_history_not_found(tmp_db: Path) -> None:
    with pytest.raises(core.CardNotFoundError):
        core.get_review_history(tmp_db, 9999)


# --- FSRS Status tests ---


def test_get_fsrs_status_default(tmp_db: Path) -> None:
    status = core.get_fsrs_status(tmp_db)
    assert status.is_default
    assert status.review_count == 0
    assert status.min_reviews_needed == 512
    assert not status.can_optimize


def test_optimize_insufficient_reviews(tmp_db: Path) -> None:
    """With very few reviews, optimization runs but can't meaningfully improve params."""
    result_card = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result_card["card_id"])
    review = ReviewInput(card_id=card_id, rating=3)
    core.submit_review(tmp_db, review)

    result = core.optimize_parameters(tmp_db)
    assert result.review_count == 1


def test_params_loaded_from_config(tmp_db: Path) -> None:
    import json

    from spacedrep import db as _db
    from spacedrep import fsrs_engine

    # Store custom params in config
    conn = _db.get_connection(tmp_db)
    custom_params = list(fsrs_engine.DEFAULT_PARAMS)
    custom_params[0] = 0.1234  # Modify first param
    _db.set_config(conn, "fsrs_parameters", json.dumps(custom_params))
    conn.commit()
    conn.close()

    # Reset and re-open -- should load custom params
    core.reset_params_loaded()
    status = core.get_fsrs_status(tmp_db)
    assert not status.is_default
    assert abs(status.parameters[0] - 0.1234) < 0.001


# --- Anki compatibility import tests ---


def test_open_deck_cloze(tmp_db: Path, cloze_apkg: Path) -> None:
    """open_deck replaces DB with .apkg contents, including cloze cards."""
    result = core.open_deck(tmp_db, cloze_apkg, force=True)
    assert result.card_count == 2
    assert "Default" in result.decks


def test_open_deck_basic_reversed(tmp_db: Path, basic_reversed_apkg: Path) -> None:
    """open_deck loads basic+reversed cards from .apkg."""
    result = core.open_deck(tmp_db, basic_reversed_apkg, force=True)
    assert result.card_count == 2


def test_open_deck_suspended(tmp_db: Path, suspended_apkg: Path) -> None:
    """open_deck preserves suspension status from the .apkg."""
    result = core.open_deck(tmp_db, suspended_apkg, force=True)
    assert result.card_count == 2
    cards = core.list_cards(tmp_db).cards
    assert len(cards) == 2


def test_open_deck_mixed(tmp_db: Path, mixed_apkg: Path) -> None:
    """open_deck loads mixed .apkg with cloze, reversed, and suspended cards."""
    result = core.open_deck(tmp_db, mixed_apkg, force=True)
    assert result.card_count == 5
    assert result.deck_count >= 2


def test_open_deck_idempotent(tmp_db: Path, cloze_apkg: Path) -> None:
    """Re-opening the same .apkg produces the same result."""
    first = core.open_deck(tmp_db, cloze_apkg, force=True)
    second = core.open_deck(tmp_db, cloze_apkg, force=True)
    assert first.card_count == second.card_count


def test_open_deck_safety_check(tmp_db: Path, cloze_apkg: Path) -> None:
    """open_deck without force errors if DB already has cards."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    with pytest.raises(core.ApkgImportError):
        core.open_deck(tmp_db, cloze_apkg, force=False)


def test_open_deck_force_overrides_safety(tmp_db: Path, cloze_apkg: Path) -> None:
    """open_deck with force=True replaces DB even if it has cards."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    result = core.open_deck(tmp_db, cloze_apkg, force=True)
    assert result.card_count == 2


# --- Cloze creation tests ---


class TestAddClozeNote:
    def test_single_cloze(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::Ottawa}} is a city")
        assert result.card_count == 1
        assert len(result.card_ids) == 1
        assert result.deck == "Default"

    def test_multi_cloze(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(
            tmp_db, "{{c1::Ottawa}} is the capital of {{c2::Canada}}", deck="Geo"
        )
        assert result.card_count == 2
        assert len(result.card_ids) == 2
        assert result.deck == "Geo"

    def test_cloze_card_content(self, tmp_db: Path) -> None:
        core.add_cloze_note(tmp_db, "{{c1::Ottawa}} is the capital of {{c2::Canada}}")
        cards = core.list_cards(tmp_db)
        questions = sorted(c.question for c in cards.cards)
        assert "[...]" in questions[0]  # one card blanks Ottawa
        assert "[...]" in questions[1]  # other card blanks Canada

    def test_cloze_with_hint(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::Ottawa::capital}} is in Canada")
        assert result.card_count == 1
        detail = core.get_card_detail(tmp_db, result.card_ids[0])
        assert "[capital]" in detail.question

    def test_same_number_multiple_blanks(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::A}} and {{c1::B}} are letters")
        assert result.card_count == 1  # same cloze number -> 1 card

    def test_no_markers_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.NoClozeMarkersError):
            core.add_cloze_note(tmp_db, "No cloze markers here")

    def test_dedup_resubmit(self, tmp_db: Path) -> None:
        text = "{{c1::Ottawa}} is the capital of {{c2::Canada}}"
        first = core.add_cloze_note(tmp_db, text)
        second = core.add_cloze_note(tmp_db, text)
        assert first.card_ids == second.card_ids
        assert core.list_cards(tmp_db).total == 2  # no duplicates

    def test_orphan_cleanup(self, tmp_db: Path) -> None:
        text_v1 = "{{c1::A}} and {{c2::B}} and {{c3::C}}"
        result_v1 = core.add_cloze_note(tmp_db, text_v1)
        assert result_v1.card_count == 3

        # Re-submit with fewer cloze numbers using update
        core.update_cloze_note(tmp_db, result_v1.card_ids[0], "{{c1::A}} and {{c2::B}}")
        assert core.list_cards(tmp_db).total == 2  # c3 card deleted

    def test_tags_applied(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::test}}", tags="geo canada")
        detail = core.get_card_detail(tmp_db, result.card_ids[0])
        assert detail.tags == "geo canada"

    def test_empty_cloze_content_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.NoClozeMarkersError):
            core.add_cloze_note(tmp_db, "{{c1::}}")

    def test_c0_only_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.NoClozeMarkersError):
            core.add_cloze_note(tmp_db, "{{c0::zero}}")

    def test_c0_ignored_when_valid_markers_exist(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c0::zero}} and {{c1::real}}")
        assert result.card_count == 1  # only c1 creates a card


class TestUpdateClozeNote:
    def test_update_preserves_ids(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}} and {{c2::B}}")
        updated = core.update_cloze_note(tmp_db, original.card_ids[0], "{{c1::X}} and {{c2::Y}}")
        assert updated.card_ids == original.card_ids
        assert updated.note_id == original.note_id

    def test_update_adds_new_ordinals(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}}")
        updated = core.update_cloze_note(tmp_db, original.card_ids[0], "{{c1::A}} and {{c2::B}}")
        assert updated.card_count == 2

    def test_update_removes_ordinals(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}} and {{c2::B}}")
        updated = core.update_cloze_note(tmp_db, original.card_ids[0], "{{c1::A}}")
        assert updated.card_count == 1

    def test_update_tags_none_preserves(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}}", tags="original_tag")
        core.update_cloze_note(tmp_db, original.card_ids[0], "{{c1::B}}", tags=None)
        detail = core.get_card_detail(tmp_db, original.card_ids[0])
        assert detail.tags == "original_tag"

    def test_update_tags_changes(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}}", tags="old")
        core.update_cloze_note(tmp_db, original.card_ids[0], "{{c1::B}}", tags="new")
        detail = core.get_card_detail(tmp_db, original.card_ids[0])
        assert detail.tags == "new"

    def test_update_not_cloze_raises(self, tmp_db: Path) -> None:
        result = core.add_card(tmp_db, "Q", "A")
        card_id = int(result["card_id"])
        with pytest.raises(core.NotAClozeNoteError):
            core.update_cloze_note(tmp_db, card_id, "{{c1::X}}")

    def test_update_card_not_found_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.CardNotFoundError):
            core.update_cloze_note(tmp_db, 99999, "{{c1::X}}")

    def test_update_no_markers_raises(self, tmp_db: Path) -> None:
        original = core.add_cloze_note(tmp_db, "{{c1::A}}")
        with pytest.raises(core.NoClozeMarkersError):
            core.update_cloze_note(tmp_db, original.card_ids[0], "no markers")


class TestBulkMixed:
    def test_mixed_basic_and_cloze(self, tmp_db: Path) -> None:
        from spacedrep.models import BulkCardInput

        cards = [
            BulkCardInput(question="Q1", answer="A1", type="basic"),
            BulkCardInput(
                question="{{c1::Ottawa}} is the capital of {{c2::Canada}}",
                type="cloze",
            ),
        ]
        result = core.add_cards_bulk(tmp_db, cards)
        assert result.total == 3  # 1 basic + 2 cloze cards

    def test_basic_empty_answer_rejected(self) -> None:
        from pydantic import ValidationError

        from spacedrep.models import BulkCardInput

        with pytest.raises(ValidationError):
            BulkCardInput(question="Q", answer="", type="basic")

    def test_cloze_empty_answer_allowed(self) -> None:
        from spacedrep.models import BulkCardInput

        card = BulkCardInput(question="{{c1::X}}", type="cloze")
        assert card.answer == ""


# --- Search and filter tests ---


class TestSearchFilter:
    def test_search_matches_question(self, populated_db: Path) -> None:
        result = core.list_cards(populated_db, search="CAP theorem")
        assert result.total == 1

    def test_search_matches_answer(self, populated_db: Path) -> None:
        result = core.list_cards(populated_db, search="archival storage")
        assert result.total == 1

    def test_search_case_insensitive(self, populated_db: Path) -> None:
        result = core.list_cards(populated_db, search="cap theorem")
        assert result.total == 1

    def test_search_composes_with_deck(self, populated_db_multi_deck: Path) -> None:
        result = core.list_cards(populated_db_multi_deck, search="Lambda", deck="AWS")
        assert result.total == 1

    def test_search_no_matches(self, populated_db: Path) -> None:
        result = core.list_cards(populated_db, search="nonexistent_term_xyz")
        assert result.total == 0

    def test_search_wildcards_escaped(self, tmp_db: Path) -> None:
        core.add_card(tmp_db, "100% correct", "answer")
        core.add_card(tmp_db, "other card", "other answer")
        # '%' in search should be treated as literal
        result = core.list_cards(tmp_db, search="100%")
        assert result.total == 1

    def test_search_in_get_next_card(self, populated_db_multi_deck: Path) -> None:
        result = core.get_next_card(populated_db_multi_deck, search="Lambda")
        assert result is not None
        assert "Lambda" in result.question

    def test_search_cloze_text(self, tmp_db: Path) -> None:
        """Search finds cloze cards by their text content (stored in note flds)."""
        core.add_cloze_note(tmp_db, "{{c1::Ottawa}} is a city")
        # Search for the cloze answer text
        result = core.list_cards(tmp_db, search="Ottawa")
        assert result.total == 1


class TestSuspendedFilter:
    def test_suspended_true(self, tmp_db: Path) -> None:
        core.add_card(tmp_db, "Q1", "A1")
        result = core.add_card(tmp_db, "Q2", "A2")
        core.suspend_card(tmp_db, int(result["card_id"]))

        suspended = core.list_cards(tmp_db, suspended=True)
        assert suspended.total == 1

    def test_suspended_false(self, tmp_db: Path) -> None:
        core.add_card(tmp_db, "Q1", "A1")
        result = core.add_card(tmp_db, "Q2", "A2")
        core.suspend_card(tmp_db, int(result["card_id"]))

        active = core.list_cards(tmp_db, suspended=False)
        assert active.total == 1

    def test_suspended_none_returns_all(self, tmp_db: Path) -> None:
        core.add_card(tmp_db, "Q1", "A1")
        result = core.add_card(tmp_db, "Q2", "A2")
        core.suspend_card(tmp_db, int(result["card_id"]))

        all_cards = core.list_cards(tmp_db)
        assert all_cards.total == 2


# --- Cloze preserves suspended ---


class TestClozePreservesSuspended:
    def test_update_cloze_preserves_suspended(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::A}} and {{c2::B}}")
        core.suspend_card(tmp_db, result.card_ids[0])
        core.update_cloze_note(tmp_db, result.card_ids[1], "{{c1::X}} and {{c2::Y}}")
        detail = core.get_card_detail(tmp_db, result.card_ids[0])
        assert detail.suspended is True

    def test_add_cloze_idempotent_preserves_suspended(self, tmp_db: Path) -> None:
        text = "{{c1::A}} and {{c2::B}}"
        result = core.add_cloze_note(tmp_db, text)
        core.suspend_card(tmp_db, result.card_ids[0])
        # Re-add same text (idempotent path)
        core.add_cloze_note(tmp_db, text)
        detail = core.get_card_detail(tmp_db, result.card_ids[0])
        assert detail.suspended is True

    def test_new_cloze_ordinal_not_suspended(self, tmp_db: Path) -> None:
        result = core.add_cloze_note(tmp_db, "{{c1::A}}")
        core.suspend_card(tmp_db, result.card_ids[0])
        updated = core.update_cloze_note(tmp_db, result.card_ids[0], "{{c1::A}} and {{c2::B}}")
        # Existing card stays suspended
        detail_c1 = core.get_card_detail(tmp_db, updated.card_ids[0])
        assert detail_c1.suspended is True
        # New ordinal is not suspended
        detail_c2 = core.get_card_detail(tmp_db, updated.card_ids[1])
        assert detail_c2.suspended is False


# --- Multi-tag OR filter tests ---


def test_list_cards_multi_tag_or(populated_db_multi_deck: Path) -> None:
    """Multiple tags use OR logic: cards matching ANY tag are returned."""
    result = core.list_cards(populated_db_multi_deck, tags=["trees", "s3"])
    assert result.total >= 2
    tag_sets = [c.tags for c in result.cards]
    assert any("trees" in t for t in tag_sets)
    assert any("s3" in t for t in tag_sets)


def test_get_next_card_multi_tag_or(populated_db_multi_deck: Path) -> None:
    """get_next_card with multiple tags returns a card matching any tag."""
    result = core.get_next_card(populated_db_multi_deck, tags=["trees", "s3"])
    assert result is not None
    assert "trees" in result.tags or "s3" in result.tags


# --- Bug fix tests: input validation ---


def test_bury_card_negative_hours(tmp_db: Path) -> None:
    """bury_card rejects negative hours."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    due = core.get_next_card(tmp_db)
    assert due is not None
    with pytest.raises(core.InvalidBuryDurationError):
        core.bury_card(tmp_db, due.card_id, hours=-1)


def test_bury_card_zero_hours(tmp_db: Path) -> None:
    """bury_card rejects zero hours."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    due = core.get_next_card(tmp_db)
    assert due is not None
    with pytest.raises(core.InvalidBuryDurationError):
        core.bury_card(tmp_db, due.card_id, hours=0)


def test_bury_card_valid_hours(tmp_db: Path) -> None:
    """bury_card accepts positive hours."""
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    bury_result = core.bury_card(tmp_db, card_id, hours=1)
    assert bury_result["card_id"] == card_id
    assert "buried_until" in bury_result


def test_add_card_empty_question(tmp_db: Path) -> None:
    """add_card rejects empty question."""
    with pytest.raises(core.EmptyFieldError, match="question"):
        core.add_card(tmp_db, "", "A", deck="Test")


def test_add_card_whitespace_question(tmp_db: Path) -> None:
    """add_card rejects whitespace-only question."""
    with pytest.raises(core.EmptyFieldError, match="question"):
        core.add_card(tmp_db, "   ", "A", deck="Test")


def test_add_card_empty_answer(tmp_db: Path) -> None:
    """add_card rejects empty answer."""
    with pytest.raises(core.EmptyFieldError, match="answer"):
        core.add_card(tmp_db, "Q", "", deck="Test")


def test_add_card_whitespace_answer(tmp_db: Path) -> None:
    """add_card rejects whitespace-only answer."""
    with pytest.raises(core.EmptyFieldError, match="answer"):
        core.add_card(tmp_db, "Q", "   \t\n", deck="Test")


def test_list_cards_negative_limit_clamped(populated_db_multi_deck: Path) -> None:
    """list_cards clamps negative limit to 1."""
    result = core.list_cards(populated_db_multi_deck, limit=-1)
    assert result.limit == 1
    assert len(result.cards) == 1


def test_list_cards_negative_offset_clamped(populated_db_multi_deck: Path) -> None:
    """list_cards clamps negative offset to 0."""
    result = core.list_cards(populated_db_multi_deck, offset=-5)
    assert result.offset == 0


def test_list_cards_excessive_limit_clamped(populated_db_multi_deck: Path) -> None:
    """list_cards clamps excessive limit to 1000."""
    result = core.list_cards(populated_db_multi_deck, limit=10000)
    assert result.limit == 1000


def test_submit_review_suspended_card(tmp_db: Path) -> None:
    """submit_review rejects reviews on suspended cards."""
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    core.suspend_card(tmp_db, card_id)
    review = ReviewInput(card_id=card_id, rating=3)
    with pytest.raises(core.CardSuspendedError):
        core.submit_review(tmp_db, review)


def test_submit_review_after_unsuspend(tmp_db: Path) -> None:
    """submit_review works after unsuspending a card."""
    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])
    core.suspend_card(tmp_db, card_id)
    core.unsuspend_card(tmp_db, card_id)
    review = ReviewInput(card_id=card_id, rating=3)
    review_result = core.submit_review(tmp_db, review)
    assert review_result.rating == "good"


def test_submit_review_buries_cloze_siblings(tmp_db: Path) -> None:
    """Reviewing one cloze sibling buries the others until tomorrow."""
    cloze = core.add_cloze_note(
        tmp_db, "{{c1::Ottawa}} is the capital of {{c2::Canada}}", deck="Geo"
    )
    assert cloze.card_count == 2
    first_id, second_id = cloze.card_ids[0], cloze.card_ids[1]

    review = ReviewInput(card_id=first_id, rating=3)
    result = core.submit_review(tmp_db, review)

    assert result.siblings_buried == [second_id]
    # Sibling should be excluded from the queue.
    nxt = core.get_next_card(tmp_db, deck="Geo")
    assert nxt is None or nxt.card_id != second_id


def test_submit_review_buries_reversed_sibling(tmp_db: Path) -> None:
    """Reviewing one card of a reversed pair buries its sibling."""
    rev = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
    assert rev.card_count == 2
    first_id, second_id = rev.card_ids[0], rev.card_ids[1]

    review = ReviewInput(card_id=first_id, rating=3)
    result = core.submit_review(tmp_db, review)

    assert result.siblings_buried == [second_id]


def test_submit_review_no_siblings_unaffected(tmp_db: Path) -> None:
    """A basic single-card note has no siblings to bury."""
    added = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(added["card_id"])

    review = ReviewInput(card_id=card_id, rating=3)
    result = core.submit_review(tmp_db, review)

    assert result.siblings_buried == []


def test_get_next_card_includes_recent_reviews(tmp_db: Path) -> None:
    """card next returns up to the last INLINE_HISTORY_LIMIT reviews inline."""
    from spacedrep import db as db_mod

    added = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(added["card_id"])

    for i in range(4):
        core.submit_review(tmp_db, ReviewInput(card_id=card_id, rating=3, user_answer=f"a{i}"))

    # Force the card back to due so card next surfaces it.
    conn = db_mod.get_connection(tmp_db)
    conn.execute("UPDATE cards SET due = 0, type = 2, queue = 2 WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()

    due = core.get_next_card(tmp_db, deck="Test")
    assert due is not None
    assert due.card_id == card_id
    assert len(due.recent_reviews) == 3
    # Chronological order: oldest of the 3 first.
    assert [r.user_answer for r in due.recent_reviews] == ["a1", "a2", "a3"]


def test_get_next_card_new_card_empty_history(tmp_db: Path) -> None:
    """Brand-new card has no review history yet."""
    core.add_card(tmp_db, "Q", "A", deck="Test")
    due = core.get_next_card(tmp_db, deck="Test")
    assert due is not None
    assert due.recent_reviews == []


def test_get_next_card_due_remaining(tmp_db: Path) -> None:
    """due_remaining reflects total still-due, including the returned card."""
    for i in range(5):
        core.add_card(tmp_db, f"Q{i}", f"A{i}", deck="Test")

    due = core.get_next_card(tmp_db, deck="Test")
    assert due is not None
    assert due.due_remaining == 5


def test_get_next_card_due_remaining_respects_filters(tmp_db: Path) -> None:
    """due_remaining honors the same filters as the SELECT (deck filter here)."""
    for i in range(3):
        core.add_card(tmp_db, f"QA{i}", f"A{i}", deck="DeckA")
    for i in range(2):
        core.add_card(tmp_db, f"QB{i}", f"A{i}", deck="DeckB")

    due_a = core.get_next_card(tmp_db, deck="DeckA")
    assert due_a is not None
    assert due_a.due_remaining == 3

    due_b = core.get_next_card(tmp_db, deck="DeckB")
    assert due_b is not None
    assert due_b.due_remaining == 2


def test_get_card_includes_recent_reviews(tmp_db: Path) -> None:
    """get_card_detail returns the same recent_reviews field."""
    added = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(added["card_id"])

    for i in range(2):
        core.submit_review(tmp_db, ReviewInput(card_id=card_id, rating=3, feedback=f"f{i}"))

    detail = core.get_card_detail(tmp_db, card_id)
    assert len(detail.recent_reviews) == 2
    assert [r.feedback for r in detail.recent_reviews] == ["f0", "f1"]


# ---------------------------------------------------------------------------
# Bug fix regression tests
# ---------------------------------------------------------------------------


def test_revlog_interval_and_type(tmp_db: Path) -> None:
    """revlog.ivl should reflect scheduled interval; revlog.type should reflect card state."""
    from spacedrep import db

    result = core.add_card(tmp_db, "Q", "A", deck="Test")
    card_id = int(result["card_id"])

    # First review (new/learning card) -> revlog.type should be 0 (learn)
    review = ReviewInput(card_id=card_id, rating=3)
    core.submit_review(tmp_db, review)

    conn = db.get_connection(tmp_db)
    row = conn.execute(
        "SELECT ivl, lastIvl, type FROM revlog WHERE cid = ? ORDER BY id LIMIT 1",
        (card_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["type"] == 0  # learn

    # Move to Review state and review again -> revlog.type should be 1 (review)
    _review_card_to_review_state(tmp_db, card_id)
    detail = core.get_card_detail(tmp_db, card_id)
    assert detail.state == "review"

    review = ReviewInput(card_id=card_id, rating=3)
    core.submit_review(tmp_db, review)

    conn = db.get_connection(tmp_db)
    row = conn.execute(
        "SELECT ivl, lastIvl, type FROM revlog WHERE cid = ? ORDER BY id DESC LIMIT 1",
        (card_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["type"] == 1  # review
    assert row["ivl"] > 0  # should have a non-zero interval


class TestClozeOrdinals:
    def test_non_contiguous_ordinals(self, tmp_db: Path) -> None:
        """Non-contiguous cloze numbers should produce correct ordinals."""
        from spacedrep import db

        result = core.add_cloze_note(tmp_db, "{{c2::X}} and {{c5::Y}}", deck="Test")
        assert result.card_count == 2

        conn = db.get_connection(tmp_db)
        ords = [
            r["ord"]
            for r in conn.execute(
                "SELECT ord FROM cards WHERE nid = ? ORDER BY ord",
                (result.note_id,),
            ).fetchall()
        ]
        conn.close()
        assert ords == [1, 4]  # c2 -> ord=1, c5 -> ord=4

    def test_update_all_ordinals_changed(self, tmp_db: Path) -> None:
        """Changing all cloze ordinals should not corrupt the note."""
        original = core.add_cloze_note(tmp_db, "{{c1::A}} and {{c2::B}}", deck="Test")
        card_id = original.card_ids[0]

        updated = core.update_cloze_note(tmp_db, card_id, "{{c3::X}} and {{c4::Y}}")
        assert updated.card_count == 2

        # Verify cards are accessible
        for cid in updated.card_ids:
            detail = core.get_card_detail(tmp_db, cid)
            assert detail is not None
            assert detail.deck == "Test"


def test_save_deck_strips_extension_tables(tmp_db: Path) -> None:
    """Exported .apkg should not contain spacedrep extension tables."""
    import sqlite3
    import tempfile
    import zipfile

    core.add_card(tmp_db, "Q", "A", deck="Test")
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "export.apkg"
        core.save_deck(tmp_db, out)

        # Extract and verify no extension tables
        with zipfile.ZipFile(str(out)) as zf:
            zf.extract("collection.anki21", tmpdir)

        conn = sqlite3.connect(str(Path(tmpdir) / "collection.anki21"))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'spacedrep%'"
            ).fetchall()
        }
        conn.close()
        assert len(tables) == 0


def test_list_cards_retrievability_filter(tmp_db: Path) -> None:
    """Retrievability filter should actually filter cards."""
    result = core.add_card(tmp_db, "Q1", "A1", deck="Test")
    card_id = int(result["card_id"])

    # New card has retrievability=0, so min_retrievability=0.5 should exclude it
    cards = core.list_cards(tmp_db, min_retrievability=0.5)
    assert cards.total == 0

    # Review the card to give it retrievability > 0
    review = ReviewInput(card_id=card_id, rating=4)  # easy
    core.submit_review(tmp_db, review)

    # Now it should appear with min_retrievability=0.5
    cards = core.list_cards(tmp_db, min_retrievability=0.5)
    assert cards.total == 1

    # But not with min_retrievability=1.1 (impossible value)
    cards = core.list_cards(tmp_db, min_retrievability=1.1)
    assert cards.total == 0


# --- Reversed card creation tests ---


class TestAddReversedCard:
    def test_creates_two_cards_one_note(self, tmp_db: Path) -> None:
        """add_reversed_card produces 2 cards from 1 note with swapped Q/A."""
        result = core.add_reversed_card(
            tmp_db, "Capital of France", "Paris", deck="geo", tags="geography"
        )
        assert result.card_count == 2
        assert len(result.card_ids) == 2
        assert result.card_ids[0] != result.card_ids[1]
        assert result.deck == "geo"

        # Forward card: Q="Capital of France", A="Paris"
        d0 = core.get_card_detail(tmp_db, result.card_ids[0])
        assert d0.question == "Capital of France"
        assert d0.answer == "Paris"
        assert d0.deck == "geo"
        assert d0.tags == "geography"

        # Reversed card: Q="Paris", A="Capital of France"
        d1 = core.get_card_detail(tmp_db, result.card_ids[1])
        assert d1.question == "Paris"
        assert d1.answer == "Capital of France"

    def test_dedup_updates_in_place(self, tmp_db: Path) -> None:
        """Re-adding with same (question, deck) updates the note; card_ids stable."""
        r1 = core.add_reversed_card(tmp_db, "Q", "A1", deck="d")
        r2 = core.add_reversed_card(tmp_db, "Q", "A2", deck="d")
        assert r1.card_ids == r2.card_ids
        assert r1.note_id == r2.note_id

        # Both cards reflect the new answer text
        assert core.get_card_detail(tmp_db, r2.card_ids[0]).answer == "A2"
        assert core.get_card_detail(tmp_db, r2.card_ids[1]).question == "A2"

    def test_dedup_preserves_review_history(self, tmp_db: Path) -> None:
        """Updating the answer via re-add does not drop review history."""
        r = core.add_reversed_card(tmp_db, "Q", "A1", deck="d")
        forward_id = r.card_ids[0]
        core.submit_review(tmp_db, ReviewInput(card_id=forward_id, rating=3))

        core.add_reversed_card(tmp_db, "Q", "A2", deck="d")
        history = core.get_review_history(tmp_db, forward_id)
        assert history.total == 1

    def test_empty_tags_preserves_existing(self, tmp_db: Path) -> None:
        """Re-adding with empty tags does not clobber existing tags."""
        r1 = core.add_reversed_card(tmp_db, "Q", "A", deck="d", tags="t1 t2")
        core.add_reversed_card(tmp_db, "Q", "A-new", deck="d")  # no tags
        d = core.get_card_detail(tmp_db, r1.card_ids[0])
        assert d.tags == "t1 t2"

    def test_empty_answer_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.EmptyFieldError, match="answer"):
            core.add_reversed_card(tmp_db, "Q", "", deck="d")

    def test_empty_question_raises(self, tmp_db: Path) -> None:
        with pytest.raises(core.EmptyFieldError, match="question"):
            core.add_reversed_card(tmp_db, "", "A", deck="d")

    def test_different_decks_are_separate_notes(self, tmp_db: Path) -> None:
        r1 = core.add_reversed_card(tmp_db, "Q", "A", deck="d1")
        r2 = core.add_reversed_card(tmp_db, "Q", "A", deck="d2")
        assert r1.note_id != r2.note_id
        assert set(r1.card_ids).isdisjoint(r2.card_ids)


class TestAddCardsBulkReversed:
    def test_bulk_mixed_types(self, tmp_db: Path) -> None:
        """Bulk can mix basic, cloze, and reversed entries in one transaction."""
        from spacedrep.models import BulkCardInput

        cards = [
            BulkCardInput(question="Basic Q", answer="Basic A", deck="d"),
            BulkCardInput(question="{{c1::cloze}}", type="cloze", deck="d"),
            BulkCardInput(question="Rev Q", answer="Rev A", type="reversed", deck="d"),
        ]
        result = core.add_cards_bulk(tmp_db, cards)
        # 1 basic + 1 cloze + 2 reversed = 4
        assert result.total == 4
        assert len(result.created) == 4

    def test_bulk_reversed_missing_answer_raises(self, tmp_db: Path) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Reversed cards require"):
            # Validator rejects at construction time
            from spacedrep.models import BulkCardInput

            BulkCardInput(question="Q", answer="", type="reversed", deck="d")

    def test_bulk_reversed_dedup_in_same_batch(self, tmp_db: Path) -> None:
        """Two reversed entries with same (question, deck) within one bulk
        call hit the dedup path. Expect: one pair of cards (total=2), no
        duplicate ids in `created`, and only 2 cards in the DB after.
        """
        from spacedrep.models import BulkCardInput

        cards = [
            BulkCardInput(question="DupQ", answer="A1", type="reversed", deck="bulk"),
            BulkCardInput(question="DupQ", answer="A2", type="reversed", deck="bulk"),
        ]
        result = core.add_cards_bulk(tmp_db, cards)
        assert result.total == 2
        assert len(result.created) == 2
        assert result.created[0] != result.created[1]
        listed = core.list_cards(tmp_db, deck="bulk")
        assert listed.total == 2


# --- Template-aware update_card tests ---


class TestUpdateCardTemplateAware:
    def test_basic_card_positional_semantics_unchanged(self, tmp_db: Path) -> None:
        """Basic (single-template) cards still update question→field 0, answer→field 1."""
        r = core.add_card(tmp_db, "Q-old", "A-old", deck="d")
        cid = int(r["card_id"])
        detail = core.update_card(tmp_db, cid, question="Q-new", answer="A-new")
        assert detail.question == "Q-new"
        assert detail.answer == "A-new"

    def test_reversed_ord0_question_updates_its_front(self, tmp_db: Path) -> None:
        """Editing the forward card's question updates its front and the
        reversed card's back (shared-note invariant)."""
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        forward, reverse = r.card_ids
        core.update_card(tmp_db, forward, question="Q-new")
        assert core.get_card_detail(tmp_db, forward).question == "Q-new"
        assert core.get_card_detail(tmp_db, reverse).answer == "Q-new"

    def test_reversed_ord1_question_updates_that_cards_front(self, tmp_db: Path) -> None:
        """Editing the reversed card's question updates *that* card's front
        (the Answer field), not the other card's front. Bug-fix pin."""
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        forward, reverse = r.card_ids
        core.update_card(tmp_db, reverse, question="new-front")
        # The reversed card's rendered front (its "question") is now "new-front"
        assert core.get_card_detail(tmp_db, reverse).question == "new-front"
        # And the forward card's back reflects the same underlying field
        assert core.get_card_detail(tmp_db, forward).answer == "new-front"
        # The forward card's front is unchanged
        assert core.get_card_detail(tmp_db, forward).question == "Q"

    def test_reversed_ord1_answer_updates_that_cards_back(self, tmp_db: Path) -> None:
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        forward, reverse = r.card_ids
        core.update_card(tmp_db, reverse, answer="new-back")
        assert core.get_card_detail(tmp_db, reverse).answer == "new-back"
        assert core.get_card_detail(tmp_db, forward).question == "new-back"

    def test_cloze_card_raises_update_cloze_card_error(self, tmp_db: Path) -> None:
        """update_card on a cloze card points the caller at update_cloze_note."""
        r = core.add_cloze_note(tmp_db, "{{c1::Ottawa}} is a city", deck="d")
        cid = r.card_ids[0]
        with pytest.raises(core.UpdateClozeCardError):
            core.update_card(tmp_db, cid, question="new")

    def test_imported_reversed_deck_update_is_template_aware(
        self, tmp_db: Path, basic_reversed_apkg: Path
    ) -> None:
        """Editing an imported reversed card's front updates that card, not
        the other direction. Pins the fix for apkg-imported decks too."""
        core.open_deck(tmp_db, basic_reversed_apkg, force=True)
        cards = core.list_cards(tmp_db).cards
        assert len(cards) == 2
        # Fixture: forward Q="What is Python?", reverse Q="A programming language"
        by_q = {c.question: c.card_id for c in cards}
        forward = by_q["What is Python?"]
        reverse = by_q["A programming language"]

        core.update_card(tmp_db, reverse, question="a programming language (edited)")
        assert core.get_card_detail(tmp_db, reverse).question == "a programming language (edited)"
        assert core.get_card_detail(tmp_db, forward).answer == "a programming language (edited)"


# --- Reversed card lifecycle tests ---


class TestReversedLifecycle:
    def test_delete_one_half_keeps_other(self, tmp_db: Path) -> None:
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        forward, reverse = r.card_ids
        core.delete_card(tmp_db, forward)

        # Reverse card still exists and renders correctly
        d = core.get_card_detail(tmp_db, reverse)
        assert d.question == "A"

        # Forward is gone
        with pytest.raises(core.CardNotFoundError):
            core.get_card_detail(tmp_db, forward)

    def test_delete_both_removes_note(self, tmp_db: Path) -> None:
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        for cid in r.card_ids:
            core.delete_card(tmp_db, cid)
        for cid in r.card_ids:
            with pytest.raises(core.CardNotFoundError):
                core.get_card_detail(tmp_db, cid)

    def test_suspend_one_half_independent(self, tmp_db: Path) -> None:
        """Suspending one card of the pair does not suspend the other."""
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        forward, reverse = r.card_ids
        core.suspend_card(tmp_db, forward)
        assert core.get_card_detail(tmp_db, forward).suspended is True
        assert core.get_card_detail(tmp_db, reverse).suspended is False

    def test_roundtrip_export_import(self, tmp_db: Path) -> None:
        """Create reversed pair, save_deck, open_deck — both directions render."""
        core.add_reversed_card(tmp_db, "Capital of France", "Paris", deck="geo")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.apkg"
            core.save_deck(tmp_db, out)

            new_db = Path(tmpdir) / "new.db"
            core.init_database(new_db)
            core.open_deck(new_db, out, force=True)
            cards = core.list_cards(new_db).cards
            assert len(cards) == 2
            questions = {c.question for c in cards}
            assert questions == {"Capital of France", "Paris"}

    def test_add_reversed_errors_on_existing_basic(self, tmp_db: Path) -> None:
        """Adding a reversed note where a basic note with the same
        (question, deck) exists raises CrossModelCollisionError instead of
        silently creating a parallel note."""
        basic = core.add_card(tmp_db, "Q", "A", deck="d")
        with pytest.raises(core.CrossModelCollisionError) as excinfo:
            core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        assert excinfo.value.error_code == "cross_model_collision"
        assert excinfo.value.extra["existing_card_id"] == basic["card_id"]
        assert "batch_index" not in excinfo.value.extra
        # No partial state — only the original basic card exists.
        assert len(core.list_cards(tmp_db).cards) == 1

    def test_add_basic_errors_on_existing_reversed(self, tmp_db: Path) -> None:
        """Adding a basic note where a reversed pair with the same
        (question, deck) exists raises CrossModelCollisionError. The
        existing_card_id points at the reversed pair's forward card
        (ord=0), which matches the sfld."""
        reversed_result = core.add_reversed_card(tmp_db, "Q", "A", deck="d")
        with pytest.raises(core.CrossModelCollisionError) as excinfo:
            core.add_card(tmp_db, "Q", "A", deck="d")
        assert excinfo.value.extra["existing_card_id"] == reversed_result.card_ids[0]
        assert "batch_index" not in excinfo.value.extra
        assert len(core.list_cards(tmp_db).cards) == 2

    def test_basic_and_reversed_in_other_deck_do_not_collide(self, tmp_db: Path) -> None:
        """Collision is scoped to the target deck. Same question in different
        decks is not a conflict."""
        core.add_card(tmp_db, "Q", "A", deck="d1")
        core.add_reversed_card(tmp_db, "Q", "A", deck="d2")
        assert len(core.list_cards(tmp_db).cards) == 3  # 1 basic + 2 reversed

    def test_bulk_raises_on_cross_model_collision(self, tmp_db: Path) -> None:
        """Cross-model collision inside a single bulk batch aborts the whole
        batch — no partial inserts survive."""
        from spacedrep.models import BulkCardInput

        batch = [
            BulkCardInput(question="Q", answer="A", deck="d", type="basic"),
            BulkCardInput(question="Q", answer="A", deck="d", type="reversed"),
        ]
        with pytest.raises(core.CrossModelCollisionError) as excinfo:
            core.add_cards_bulk(tmp_db, batch)
        # The second item (index 1) is the colliding one.
        assert excinfo.value.extra["batch_index"] == 1
        assert "[batch item 1]" in excinfo.value.message
        # Transaction rolled back; no cards committed.
        assert len(core.list_cards(tmp_db).cards) == 0

    def test_update_deck_on_reversed_moves_both(self, tmp_db: Path) -> None:
        """Moving one card of a reversed pair via update_card --deck moves its
        sibling too — siblings share one note, and single-card moves would
        split the pair (then get silently reverted on the next re-add)."""
        r = core.add_reversed_card(tmp_db, "Q", "A", deck="origin")
        forward, reverse = r.card_ids
        core.update_card(tmp_db, forward, deck="destination")
        assert core.get_card_detail(tmp_db, forward).deck == "destination"
        assert core.get_card_detail(tmp_db, reverse).deck == "destination"

    def test_update_deck_on_basic_moves_only_card(self, tmp_db: Path) -> None:
        """Single-template basic notes keep per-card deck scope — only the
        targeted card moves."""
        a_id = int(core.add_card(tmp_db, "Q1", "A1", deck="a")["card_id"])
        b_id = int(core.add_card(tmp_db, "Q2", "A2", deck="a")["card_id"])
        core.update_card(tmp_db, a_id, deck="b")
        assert core.get_card_detail(tmp_db, a_id).deck == "b"
        assert core.get_card_detail(tmp_db, b_id).deck == "a"

    def test_update_deck_on_imported_multi_template_moves_both(
        self, tmp_db: Path, basic_reversed_apkg: Path
    ) -> None:
        """Template-awareness extends to imported multi-template models, not
        just spacedrep's own reversed model."""
        core.open_deck(tmp_db, basic_reversed_apkg, force=True)
        cards = core.list_cards(tmp_db).cards
        assert len(cards) == 2
        first = cards[0].card_id
        second = cards[1].card_id
        core.update_card(tmp_db, first, deck="moved")
        assert core.get_card_detail(tmp_db, first).deck == "moved"
        assert core.get_card_detail(tmp_db, second).deck == "moved"


# ---------------------------------------------------------------------------
# Modern-schema rejection tests
# ---------------------------------------------------------------------------


class TestUnsupportedCollectionFormat:
    def test_list_cards_raises_on_modern_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / "modern.anki21"
        _write_modern_schema_db(db_path)
        with pytest.raises(core.UnsupportedCollectionFormatError) as excinfo:
            core.list_cards(db_path)
        err = excinfo.value
        assert err.error_code == "unsupported_collection_format"
        assert "notetypes" in err.message

    def test_get_card_raises_on_modern_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / "modern.anki21"
        _write_modern_schema_db(db_path)
        with pytest.raises(core.UnsupportedCollectionFormatError):
            core.get_card_detail(db_path, 1)

    def test_init_database_raises_on_modern_schema(self, tmp_path: Path) -> None:
        """init_database must also reject so users don't get a false "ok"."""
        db_path = tmp_path / "modern.anki21"
        _write_modern_schema_db(db_path)
        with pytest.raises(core.UnsupportedCollectionFormatError):
            core.init_database(db_path)

    def test_open_deck_rejects_modern_schema_apkg(self, tmp_path: Path) -> None:
        apkg = tmp_path / "modern.apkg"
        inner = tmp_path / "inner.anki21"
        _write_modern_schema_apkg(apkg, inner)
        target = tmp_path / "target.anki21"
        core.init_database(target)
        with pytest.raises(core.UnsupportedCollectionFormatError):
            core.open_deck(target, apkg, force=True)

    def test_fresh_db_is_not_flagged_as_modern(self, tmp_db: Path) -> None:
        """Sanity: the normal init path must not trip the probe."""
        # Using any core operation on tmp_db should succeed.
        result = core.list_cards(tmp_db)
        assert result.total == 0


class TestCreatedAtStability:
    def test_created_at_does_not_shift_on_update(self, tmp_db: Path) -> None:
        """created_at is derived from cards.id (ms epoch at insert), not
        cards.mod (updated on every edit). An update must not move it.
        """
        r = core.add_card(tmp_db, "q", "a", deck="d")
        cid = int(r["card_id"])
        before = core.get_card_detail(tmp_db, cid)
        assert before is not None
        core.update_card(tmp_db, cid, question="q-edited")
        after = core.get_card_detail(tmp_db, cid)
        assert after is not None
        assert after.created_at == before.created_at

    def test_created_at_stable_across_reversed_re_add(self, tmp_db: Path) -> None:
        """Reversed dedup path updates cards.mod on both cards; created_at
        on both must stay pinned to the original insert."""
        r = core.add_reversed_card(tmp_db, "Q", "A1", deck="d")
        before = [core.get_card_detail(tmp_db, cid) for cid in r.card_ids]
        assert all(d is not None for d in before)
        core.add_reversed_card(tmp_db, "Q", "A2", deck="d")
        after = [core.get_card_detail(tmp_db, cid) for cid in r.card_ids]
        for b, a in zip(before, after, strict=True):
            assert b is not None and a is not None
            assert a.created_at == b.created_at
