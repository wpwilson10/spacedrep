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
