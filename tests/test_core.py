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


def test_add_card_dedup_different_deck(tmp_db: Path) -> None:
    """Same question in different decks creates separate cards."""
    r1 = core.add_card(tmp_db, "What is X?", "A1", deck="DeckA")
    r2 = core.add_card(tmp_db, "What is X?", "A2", deck="DeckB")
    assert r1["card_id"] != r2["card_id"]


def test_add_cards_bulk_dedup(tmp_db: Path) -> None:
    """Bulk add with duplicate entries deduplicates."""
    from spacedrep.models import BulkCardInput

    cards = [
        BulkCardInput(question="Q1", answer="A1", deck="Test"),
        BulkCardInput(question="Q1", answer="A1-updated", deck="Test"),
        BulkCardInput(question="Q2", answer="A2", deck="Test"),
    ]
    result = core.add_cards_bulk(tmp_db, cards)
    assert result.count == 3  # 3 operations
    assert result.created[0] == result.created[1]  # same card_id for duplicates
    assert result.created[0] != result.created[2]  # different card


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


def test_import_cloze_produces_cards(tmp_db: Path, cloze_apkg: Path) -> None:
    """Import cloze apkg. Both cards share the same note guid, so the second
    card update overwrites the first, resulting in 1 note and 1 card in DB."""
    result = core.import_deck(tmp_db, cloze_apkg)
    # Two card records from apkg share guid "guid1": first inserts, second updates
    assert result.imported + result.updated == 2
    cards = core.list_cards(tmp_db).cards
    # With guid-based dedup, only 1 card exists (second overwrites first)
    assert len(cards) >= 1
    questions = {c.question for c in cards}
    assert any("[...]" in q for q in questions)


def test_import_cloze_content(tmp_db: Path, cloze_apkg: Path) -> None:
    """After importing cloze, the surviving card has cloze-rendered content."""
    core.import_deck(tmp_db, cloze_apkg)
    cards = core.list_cards(tmp_db).cards
    assert len(cards) >= 1
    detail = core.get_card_detail(tmp_db, cards[0].card_id)
    # The surviving card should have cloze-blanked question with [...]
    assert "[...]" in detail.question


def test_import_basic_reversed_produces_card(tmp_db: Path, basic_reversed_apkg: Path) -> None:
    """Import basic+reversed apkg. Both cards share the same note guid,
    so the second card overwrites the first, resulting in 1 card."""
    result = core.import_deck(tmp_db, basic_reversed_apkg)
    assert result.imported + result.updated == 2
    cards = core.list_cards(tmp_db).cards
    assert len(cards) >= 1


def test_import_suspended_cards(tmp_db: Path, suspended_apkg: Path) -> None:
    """Import apkg with a suspended card. The import function does not
    preserve suspension status, so both cards appear active."""
    result = core.import_deck(tmp_db, suspended_apkg)
    assert result.imported == 2
    cards = core.list_cards(tmp_db).cards
    assert len(cards) == 2
    # Import does not carry over Anki suspension status
    questions = {c.question for c in cards}
    assert "Active Q" in questions
    assert "Suspended Q" in questions


def test_import_mixed_apkg(tmp_db: Path, mixed_apkg: Path) -> None:
    """Import mixed apkg. Multi-card notes share guids, so second card of
    each multi-card note updates the first. 3 unique guids -> 3 notes/cards."""
    result = core.import_deck(tmp_db, mixed_apkg)
    # 5 card records but only 3 unique guids (guidA, guidB, guidC)
    # First of each guid: imported; second of cloze + reversed: updated
    assert result.imported == 3
    assert result.updated == 2
    cards = core.list_cards(tmp_db).cards
    assert len(cards) == 3


def test_import_dedup_composite_key(tmp_db: Path, cloze_apkg: Path) -> None:
    """Re-importing the same apkg deduplicates on notes.guid."""
    first = core.import_deck(tmp_db, cloze_apkg)
    # Two card records with same guid: 1 insert + 1 update
    assert first.imported + first.updated == 2
    total_after_first = core.list_cards(tmp_db).total

    second = core.import_deck(tmp_db, cloze_apkg)
    # Both cards now find existing note by guid -> all updates
    assert second.imported == 0
    assert second.updated == 2
    # Same number of cards as after first import
    assert core.list_cards(tmp_db).total == total_after_first


def test_import_reimport_updates_content(
    tmp_db: Path, suspended_apkg: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Re-import with changed content updates the cards."""
    from tests.conftest import BASIC_MODEL, DEFAULT_DECK, build_anki_apkg

    core.import_deck(tmp_db, suspended_apkg)
    cards_before = core.list_cards(tmp_db).cards
    assert len(cards_before) == 2

    # Build a modified apkg with same guids but different content
    tmp = tmp_path_factory.mktemp("reimport")
    modified_apkg = build_anki_apkg(
        tmp,
        "modified",
        models=BASIC_MODEL,
        decks=DEFAULT_DECK,
        notes=[
            (300, "1000", "Updated Active Q\x1fUpdated Active A", "guid3", ""),
            (301, "1000", "Updated Suspended Q\x1fUpdated Suspended A", "guid4", ""),
        ],
        cards=[
            (5, 300, 1, 0, 0),
            (6, 301, 1, 0, 0),
        ],
    )
    result = core.import_deck(tmp_db, modified_apkg)
    assert result.updated == 2

    cards_after = core.list_cards(tmp_db).cards
    assert len(cards_after) == 2
    questions = {c.question for c in cards_after}
    assert "Updated Active Q" in questions
    assert "Updated Suspended Q" in questions


def test_import_dry_run_multi_card(tmp_db: Path, mixed_apkg: Path) -> None:
    result = core.import_deck(tmp_db, mixed_apkg, dry_run=True)
    assert result.dry_run is True
    # Dry run checks notes.guid in DB (empty), so all are "would import"
    # But cards with same guid count multiple times in dry_run
    assert result.imported + result.updated >= 3
    # No cards actually written
    assert core.list_cards(tmp_db).total == 0


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
        assert result.count == 3  # 1 basic + 2 cloze cards

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
