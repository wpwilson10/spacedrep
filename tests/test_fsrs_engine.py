"""Tests for fsrs_engine module."""

from fsrs import State

from spacedrep import fsrs_engine


def test_create_new_card() -> None:
    card = fsrs_engine.create_new_card()
    assert card.state == State.Learning
    assert card.stability is None
    assert card.difficulty is None


def test_review_card_cycle() -> None:
    card = fsrs_engine.create_new_card()

    # First review: Good
    card, _log = fsrs_engine.review_card(card, 3)
    assert card.state in (State.Learning, State.Review)
    assert card.stability is not None

    # Second review: Good
    card, _log = fsrs_engine.review_card(card, 3)
    assert card.due is not None


def test_serialization_roundtrip() -> None:
    card = fsrs_engine.create_new_card()
    card, _ = fsrs_engine.review_card(card, 3)

    json_str = fsrs_engine.serialize_card(card)
    restored = fsrs_engine.deserialize_card(json_str)

    assert restored.state == card.state
    assert restored.stability == card.stability
    assert restored.difficulty == card.difficulty


def test_state_name() -> None:
    assert fsrs_engine.state_name(State.Learning, None) == "new"
    assert fsrs_engine.state_name(State.Learning, "2026-01-01") == "learning"
    assert fsrs_engine.state_name(State.Review) == "review"
    assert fsrs_engine.state_name(State.Relearning) == "relearning"


def test_rating_name() -> None:
    assert fsrs_engine.rating_name(1) == "again"
    assert fsrs_engine.rating_name(2) == "hard"
    assert fsrs_engine.rating_name(3) == "good"
    assert fsrs_engine.rating_name(4) == "easy"


def test_retrievability() -> None:
    card = fsrs_engine.create_new_card()
    # New card has 0 retrievability
    r = fsrs_engine.get_retrievability(card)
    assert r == 0.0

    # After review, should have some retrievability
    card, _ = fsrs_engine.review_card(card, 3)
    r = fsrs_engine.get_retrievability(card)
    assert r >= 0.0


# --- Preview tests ---


def test_preview_card() -> None:
    card = fsrs_engine.create_new_card()
    results = fsrs_engine.preview_card(card)
    assert len(results) == 4

    ratings = [r[0] for r in results]
    assert ratings == [1, 2, 3, 4]

    # Each result should be a different card state
    for _rating_int, updated_card, log in results:
        assert updated_card.due is not None
        assert log is not None


def test_preview_reviewed_card() -> None:
    card = fsrs_engine.create_new_card()
    card, _ = fsrs_engine.review_card(card, 3)  # Review once with "good"

    results = fsrs_engine.preview_card(card)
    assert len(results) == 4

    # Stability values should differ across ratings
    stabilities = [r[1].stability for r in results]
    assert len(set(stabilities)) > 1


# --- Scheduler management tests ---


def test_update_scheduler() -> None:
    original = fsrs_engine.get_current_parameters()
    new_params = list(original)
    new_params[0] = 0.5555

    fsrs_engine.update_scheduler(new_params)
    current = fsrs_engine.get_current_parameters()
    assert abs(current[0] - 0.5555) < 0.001

    # Restore defaults
    fsrs_engine.update_scheduler(list(fsrs_engine.DEFAULT_PARAMS))


def test_is_default_parameters() -> None:
    assert fsrs_engine.is_default_parameters()

    new_params = list(fsrs_engine.DEFAULT_PARAMS)
    new_params[0] = 0.1111
    fsrs_engine.update_scheduler(new_params)
    assert not fsrs_engine.is_default_parameters()

    # Restore
    fsrs_engine.update_scheduler(list(fsrs_engine.DEFAULT_PARAMS))


def test_get_scheduler() -> None:
    scheduler = fsrs_engine.get_scheduler()
    assert scheduler is not None
    assert scheduler.parameters == fsrs_engine.get_current_parameters()
