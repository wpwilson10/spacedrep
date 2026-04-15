"""Tests for db module (Anki-native schema)."""

from pathlib import Path

from spacedrep import db
from spacedrep.anki_schema import basic_guid


def test_init_db(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    expected = {
        "col",
        "notes",
        "cards",
        "revlog",
        "graves",
        "spacedrep_meta",
        "spacedrep_card_extra",
        "spacedrep_review_extra",
    }
    assert table_names == expected
    # Col row exists
    col = conn.execute("SELECT id FROM col WHERE id = 1").fetchone()
    assert col is not None
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
    card_id, was_update = db.insert_card(
        conn,
        question="What is X?",
        answer="X is Y.",
        deck_name="Test",
        tags="test",
        guid=basic_guid("What is X?", "Test"),
    )
    assert not was_update
    conn.commit()

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.question == "What is X?"
    assert detail.answer == "X is Y."
    conn.close()


def test_card_dedup_on_guid(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    guid = basic_guid("Original Q", "Test")

    id1, was_update1 = db.insert_card(
        conn,
        question="Original Q",
        answer="Original A",
        deck_name="Test",
        guid=guid,
    )
    assert not was_update1

    id2, was_update2 = db.insert_card(
        conn,
        question="Updated Q",
        answer="Updated A",
        deck_name="Test",
        guid=guid,
    )
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
    # Get first card's ID (not necessarily 1 since IDs are timestamp-based)
    result = db.list_cards(conn)
    first_card = result.cards[0]
    detail = db.get_card_detail(conn, first_card.card_id)
    assert detail is not None
    assert detail.card_id == first_card.card_id
    assert detail.state == "new"
    assert detail.review_count == 0
    conn.close()


def test_get_card_detail_not_found(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    detail = db.get_card_detail(conn, 9999)
    assert detail is None
    conn.close()


# --- delete_card tests ---


def test_delete_card_success(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    assert db.delete_card(conn, card_id)
    conn.commit()

    # Card should be gone
    assert db.get_card_detail(conn, card_id) is None

    # Should be in graves
    grave = conn.execute("SELECT 1 FROM graves WHERE oid = ? AND type = 0", (card_id,)).fetchone()
    assert grave is not None
    conn.close()


def test_delete_card_not_found(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    assert not db.delete_card(conn, 9999)
    conn.close()


def test_delete_card_cleans_up_review_extra(tmp_db: Path) -> None:
    """Verify delete_card removes spacedrep_review_extra rows (not just revlog)."""
    from spacedrep.models import ReviewInput

    conn = db.get_connection(tmp_db)
    card_id, _ = db.insert_card(
        conn, question="Q", answer="A", deck_name="Test", guid=basic_guid("Q", "Test")
    )

    # Simulate a review with extension data
    review = ReviewInput(card_id=card_id, rating=3, user_answer="my answer", session_id="sess1")
    db.insert_review_log(conn, review, '{"scheduled_days": 1, "elapsed_days": 0}')
    conn.commit()

    # Verify review_extra row exists
    extra = conn.execute(
        "SELECT 1 FROM spacedrep_review_extra WHERE session_id = 'sess1'"
    ).fetchone()
    assert extra is not None

    # Delete the card
    assert db.delete_card(conn, card_id)
    conn.commit()

    # Both revlog and review_extra should be gone
    revlog = conn.execute("SELECT 1 FROM revlog WHERE cid = ?", (card_id,)).fetchone()
    assert revlog is None
    extra_after = conn.execute(
        "SELECT 1 FROM spacedrep_review_extra WHERE session_id = 'sess1'"
    ).fetchone()
    assert extra_after is None
    conn.close()


def test_bury_preserves_source(tmp_db: Path) -> None:
    """Burying a card must not clobber spacedrep_card_extra.source."""
    conn = db.get_connection(tmp_db)
    card_id, _ = db.insert_card(
        conn,
        question="Q",
        answer="A",
        deck_name="Test",
        source="apkg",
        guid=basic_guid("Q", "Test"),
    )
    conn.commit()

    # Bury the card
    db.bury_card(conn, card_id, "2099-01-01 00:00:00")
    conn.commit()

    # Source should still be 'apkg'
    row = conn.execute(
        "SELECT source, buried_until FROM spacedrep_card_extra WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    assert row is not None
    assert row["source"] == "apkg"
    assert row["buried_until"] == "2099-01-01 00:00:00"
    conn.close()


def test_update_step_preserves_buried_until(tmp_db: Path) -> None:
    """Updating FSRS step must not clobber spacedrep_card_extra.buried_until."""
    from fsrs import Card

    conn = db.get_connection(tmp_db)
    card_id, _ = db.insert_card(
        conn, question="Q", answer="A", deck_name="Test", guid=basic_guid("Q", "Test")
    )

    # Bury first
    db.bury_card(conn, card_id, "2099-01-01 00:00:00")

    # Simulate FSRS state update with step > 0
    fsrs_card = Card()
    fsrs_card.step = 2
    db.update_fsrs_state(conn, card_id, fsrs_card, 0.9)
    conn.commit()

    row = conn.execute(
        "SELECT step, buried_until FROM spacedrep_card_extra WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    assert row is not None
    assert row["step"] == 2
    assert row["buried_until"] == "2099-01-01 00:00:00"
    conn.close()


# --- update_card tests ---


def test_update_card_question(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    assert db.update_card(conn, card_id, question="Updated question")
    conn.commit()

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.question == "Updated question"
    conn.close()


def test_update_card_deck(populated_db_multi_deck: Path) -> None:
    conn = db.get_connection(populated_db_multi_deck)
    result = db.list_cards(conn, deck="AWS")
    card_id = result.cards[0].card_id

    # Get DSA deck ID
    dsa_id = db.upsert_deck(conn, "DSA")
    assert db.update_card(conn, card_id, deck_id=dsa_id)
    conn.commit()

    detail = db.get_card_detail(conn, card_id)
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
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    count = db.increment_lapse_count(conn, card_id)
    assert count == 1
    count = db.increment_lapse_count(conn, card_id)
    assert count == 2
    conn.close()


def test_lapse_count_in_card_detail(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.lapse_count == 0

    db.increment_lapse_count(conn, card_id)
    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.lapse_count == 1
    conn.close()


def test_lapse_count_in_list_cards(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    db.increment_lapse_count(conn, card_id)
    result = db.list_cards(conn)
    card = next(c for c in result.cards if c.card_id == card_id)
    assert card.lapse_count == 1
    conn.close()


def test_list_cards_leech_filter(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    result = db.list_cards(conn)
    card_id = result.cards[0].card_id

    # No leeches yet
    result = db.list_cards(conn, leech_threshold=8)
    assert result.total == 0

    # Push lapse count to 8
    for _ in range(8):
        db.increment_lapse_count(conn, card_id)
    result = db.list_cards(conn, leech_threshold=8)
    assert result.total == 1
    assert result.cards[0].card_id == card_id
    conn.close()


# --- Tag hierarchy filter tests ---


def test_tag_filter_matches_children(tmp_db: Path) -> None:
    """tags=["parent"] should match cards tagged parent::child."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="Test",
        tags="foundations::bedrock::apis",
        guid=basic_guid("Q1", "Test"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="Test",
        tags="other",
        guid=basic_guid("Q2", "Test"),
    )
    conn.commit()

    result = db.list_cards(conn, tags=["foundations"])
    assert result.total == 1
    assert result.cards[0].question == "Q1"
    conn.close()


def test_tag_filter_no_cross_hierarchy(tmp_db: Path) -> None:
    """tags=["chunking"] should NOT match foundations::rag::chunking."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="Test",
        tags="foundations::rag::chunking",
        guid=basic_guid("Q1", "Test"),
    )
    conn.commit()

    result = db.list_cards(conn, tags=["chunking"])
    assert result.total == 0
    conn.close()


def test_tag_filter_multi_level(tmp_db: Path) -> None:
    """tags=["parent::child"] should match parent::child::grandchild."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="Test",
        tags="parent::child::grandchild",
        guid=basic_guid("Q1", "Test"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="Test",
        tags="parent::child",
        guid=basic_guid("Q2", "Test"),
    )
    conn.commit()

    result = db.list_cards(conn, tags=["parent::child"])
    assert result.total == 2
    conn.close()


def test_tag_filter_exact_match(tmp_db: Path) -> None:
    """tags=["AIP-C01"] should match exactly, not partial."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="Test",
        tags="AIP-C01",
        guid=basic_guid("Q1", "Test"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="Test",
        tags="AIP",
        guid=basic_guid("Q2", "Test"),
    )
    conn.commit()

    result = db.list_cards(conn, tags=["AIP-C01"])
    assert result.total == 1
    assert result.cards[0].question == "Q1"
    conn.close()


# --- Deck hierarchy filter tests ---


def test_deck_hierarchy_exact_match(tmp_db: Path) -> None:
    """Filtering by 'AWS' matches cards in the 'AWS' deck."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="AWS",
        guid=basic_guid("Q1", "AWS"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="Other",
        guid=basic_guid("Q2", "Other"),
    )
    conn.commit()
    result = db.list_cards(conn, deck="AWS")
    assert result.total == 1
    assert result.cards[0].question == "Q1"
    conn.close()


def test_deck_hierarchy_child_match(tmp_db: Path) -> None:
    """Filtering by 'AWS' also matches 'AWS::S3' and 'AWS::S3::Glacier'."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q-AWS",
        answer="A",
        deck_name="AWS",
        guid=basic_guid("Q-AWS", "AWS"),
    )
    db.insert_card(
        conn,
        question="Q-S3",
        answer="A",
        deck_name="AWS::S3",
        guid=basic_guid("Q-S3", "AWS::S3"),
    )
    db.insert_card(
        conn,
        question="Q-Glacier",
        answer="A",
        deck_name="AWS::S3::Glacier",
        guid=basic_guid("Q-Glacier", "AWS::S3::Glacier"),
    )
    conn.commit()
    result = db.list_cards(conn, deck="AWS")
    assert result.total == 3
    conn.close()


def test_deck_hierarchy_no_false_positive(tmp_db: Path) -> None:
    """Filtering by 'AWS' must not match 'AWSome' (no :: separator)."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="AWS",
        guid=basic_guid("Q1", "AWS"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="AWSome",
        guid=basic_guid("Q2", "AWSome"),
    )
    conn.commit()
    result = db.list_cards(conn, deck="AWS")
    assert result.total == 1
    assert result.cards[0].question == "Q1"
    conn.close()


def test_deck_hierarchy_child_only(tmp_db: Path) -> None:
    """Filtering by 'AWS::S3' matches 'AWS::S3' but not parent 'AWS'."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q-AWS",
        answer="A",
        deck_name="AWS",
        guid=basic_guid("Q-AWS", "AWS"),
    )
    db.insert_card(
        conn,
        question="Q-S3",
        answer="A",
        deck_name="AWS::S3",
        guid=basic_guid("Q-S3", "AWS::S3"),
    )
    conn.commit()
    result = db.list_cards(conn, deck="AWS::S3")
    assert result.total == 1
    assert result.cards[0].question == "Q-S3"
    conn.close()


# --- list_tags tests ---


def test_list_tags(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="Q1",
        answer="A1",
        deck_name="Test",
        tags="aws s3 storage",
        guid=basic_guid("Q1", "Test"),
    )
    db.insert_card(
        conn,
        question="Q2",
        answer="A2",
        deck_name="Test",
        tags="aws compute",
        guid=basic_guid("Q2", "Test"),
    )
    conn.commit()

    tags = db.list_tags(conn)
    assert tags == ["aws", "compute", "s3", "storage"]
    conn.close()


def test_list_tags_empty(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    tags = db.list_tags(conn)
    assert tags == []
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


def test_get_review_history(populated_db: Path) -> None:
    from spacedrep.models import ReviewInput

    # Submit a review to create history
    conn = db.get_connection(populated_db)
    cards = db.list_cards(conn)
    card_id = cards.cards[0].card_id
    conn.close()

    from spacedrep import core

    core.submit_review(populated_db, ReviewInput(card_id=card_id, rating=3))
    conn = db.get_connection(populated_db)
    history = db.get_review_history(conn, card_id)
    assert len(history) == 1
    assert history[0].rating == 3
    assert history[0].rating_name == "good"
    assert history[0].card_id == card_id
    conn.close()


def test_get_review_history_empty(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    result = db.get_review_history(conn, 999)
    assert result == []
    conn.close()


def test_date_filter_created_after(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    # Cards just created, so created_after far past should return them
    result = db.list_cards(conn, created_after="2000-01-01T00:00:00+00:00")
    assert result.total > 0
    # Far future should return nothing
    result = db.list_cards(conn, created_after="2099-12-31T00:00:00+00:00")
    assert result.total == 0
    conn.close()


def test_fsrs_property_filter_difficulty(populated_db: Path) -> None:
    from spacedrep import core
    from spacedrep.models import ReviewInput

    conn = db.get_connection(populated_db)
    cards = db.list_cards(conn)
    card_id = cards.cards[0].card_id
    conn.close()

    # Submit a review to generate non-zero FSRS properties
    core.submit_review(populated_db, ReviewInput(card_id=card_id, rating=3))

    conn = db.get_connection(populated_db)
    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    diff = detail.difficulty

    # Filter should include this card
    result = db.list_cards(conn, min_difficulty=diff - 0.1)
    card_ids = {c.card_id for c in result.cards}
    assert card_id in card_ids

    # Filter above the difficulty should exclude it
    result = db.list_cards(conn, min_difficulty=diff + 0.1)
    card_ids = {c.card_id for c in result.cards}
    assert card_id not in card_ids
    conn.close()


def test_fsrs_property_filter_includes_unreviewed(populated_db: Path) -> None:
    """Unreviewed cards with default difficulty=5.0 should appear in difficulty filters."""
    conn = db.get_connection(populated_db)

    # All cards are unreviewed — default difficulty is 5.0 (from factor=2500)
    cards = db.list_cards(conn)
    assert cards.total > 0
    card_id = cards.cards[0].card_id

    # Verify the card detail reports difficulty 5.0
    detail = db.get_card_detail(conn, card_id)
    assert detail is not None
    assert detail.difficulty == 5.0

    # min_difficulty=4.9 should include unreviewed cards
    result = db.list_cards(conn, min_difficulty=4.9)
    assert result.total > 0
    assert card_id in {c.card_id for c in result.cards}

    # min_difficulty=5.1 should exclude them
    result = db.list_cards(conn, min_difficulty=5.1)
    assert card_id not in {c.card_id for c in result.cards}

    # max_difficulty=5.1 should include them
    result = db.list_cards(conn, max_difficulty=5.1)
    assert card_id in {c.card_id for c in result.cards}

    # min_stability=0.0 should include unreviewed cards (stability defaults to 0)
    result = db.list_cards(conn, min_stability=0.0)
    assert card_id in {c.card_id for c in result.cards}

    conn.close()


def test_bury_card(populated_db: Path) -> None:
    conn = db.get_connection(populated_db)
    cards = db.list_cards(conn)
    card_id = cards.cards[0].card_id

    # Bury the card until far future
    assert db.bury_card(conn, card_id, "2099-12-31 23:59:59")
    conn.commit()

    # Should be excluded from get_next_due_card
    due = db.get_next_due_card(conn)
    if due is not None:
        assert due.card_id != card_id

    # Should appear with buried=True in list
    result = db.list_cards(conn, buried=True)
    buried_ids = {c.card_id for c in result.cards}
    assert card_id in buried_ids

    # Unbury
    assert db.unbury_card(conn, card_id)
    conn.commit()

    result = db.list_cards(conn, buried=True)
    assert all(c.card_id != card_id for c in result.cards)
    conn.close()


def test_bury_not_found(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    assert not db.bury_card(conn, 9999, "2099-12-31")
    assert not db.unbury_card(conn, 9999)
    conn.close()


# --- FSRS state tests ---


def test_get_fsrs_card_new(populated_db: Path) -> None:
    """New cards should have a reconstructable FSRS card."""
    from fsrs import State

    conn = db.get_connection(populated_db)
    cards = db.list_cards(conn)
    card_id = cards.cards[0].card_id

    fsrs_card = db.get_fsrs_card(conn, card_id)
    assert fsrs_card is not None
    assert fsrs_card.state == State.Learning
    assert fsrs_card.last_review is None  # never reviewed
    conn.close()


def test_get_fsrs_card_not_found(tmp_db: Path) -> None:
    conn = db.get_connection(tmp_db)
    assert db.get_fsrs_card(conn, 9999) is None
    conn.close()


def test_update_fsrs_state(populated_db: Path) -> None:
    """After review, FSRS state should be persisted in card columns."""
    from spacedrep import core
    from spacedrep.models import ReviewInput

    conn = db.get_connection(populated_db)
    cards = db.list_cards(conn)
    card_id = cards.cards[0].card_id
    conn.close()

    core.submit_review(populated_db, ReviewInput(card_id=card_id, rating=3))

    conn = db.get_connection(populated_db)
    fsrs_card = db.get_fsrs_card(conn, card_id)
    assert fsrs_card is not None
    assert fsrs_card.stability is not None
    assert fsrs_card.difficulty is not None
    assert fsrs_card.last_review is not None
    conn.close()


# ---------------------------------------------------------------------------
# Bug fix regression tests
# ---------------------------------------------------------------------------


def test_update_fsrs_state_clears_step(tmp_db: Path) -> None:
    """step=0 should be written to clear stale step values."""
    from fsrs import Card as FsrsCard

    conn = db.get_connection(tmp_db)
    card_id, _ = db.insert_card(
        conn,
        question="Step test",
        answer="A",
        deck_name="Test",
        tags="",
        guid=basic_guid("Step test", "Test"),
    )

    # Manually set step=2 in extension table
    conn.execute(
        "INSERT INTO spacedrep_card_extra (card_id, step) VALUES (?, ?)"
        " ON CONFLICT(card_id) DO UPDATE SET step = excluded.step",
        (card_id, 2),
    )
    conn.commit()

    # Verify step=2
    row = conn.execute(
        "SELECT step FROM spacedrep_card_extra WHERE card_id = ?", (card_id,)
    ).fetchone()
    assert row["step"] == 2

    # Update with a card that has step=0
    fsrs_card = FsrsCard()
    fsrs_card.step = 0
    db.update_fsrs_state(conn, card_id, fsrs_card, 0.9)
    conn.commit()

    # Verify step=0 was written
    row = conn.execute(
        "SELECT step FROM spacedrep_card_extra WHERE card_id = ?", (card_id,)
    ).fetchone()
    assert row["step"] == 0
    conn.close()


def test_due_after_includes_new_cards(tmp_db: Path) -> None:
    """due_after filter should include new cards (type=0)."""
    conn = db.get_connection(tmp_db)
    db.insert_card(
        conn,
        question="New card",
        answer="A",
        deck_name="Test",
        tags="",
        guid=basic_guid("New card", "Test"),
    )
    conn.commit()

    # New card should appear when filtering due_after a past date
    result = db.list_cards(conn, due_after="2020-01-01T00:00:00")
    assert result.total == 1

    conn.close()
