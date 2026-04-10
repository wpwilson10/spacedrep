"""Tests for apkg_writer module."""

import tempfile
from pathlib import Path

from spacedrep.apkg_writer import write_apkg
from spacedrep.models import CardRecord, DeckRecord


def test_write_apkg_basic() -> None:
    cards = [
        CardRecord(
            id=1,
            deck_id=1,
            question="What is X?",
            answer="X is Y.",
            tags="test",
            source="manual",
        ),
        CardRecord(
            id=2,
            deck_id=1,
            question="What is A?",
            answer="A is B.",
            tags="test",
            source="generated",
        ),
    ]
    decks = [DeckRecord(id=1, name="Test Deck")]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg(cards, decks, output)
        assert count == 2
        assert output.exists()
        assert output.stat().st_size > 0


def test_write_apkg_with_guid() -> None:
    cards = [
        CardRecord(
            id=1,
            deck_id=1,
            question="Q1",
            answer="A1",
            source_note_guid="abc123",
        ),
    ]
    decks = [DeckRecord(id=1, name="Test")]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg(cards, decks, output)
        assert count == 1


def test_write_apkg_empty() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg([], [], output)
        assert count == 0


def test_write_apkg_cloze_grouped() -> None:
    """Multiple cloze cards from same note become one exported note."""
    note_id = 12345
    cards = [
        CardRecord(
            id=1,
            deck_id=1,
            question="[...] is the capital of Canada",
            answer="Ottawa is the capital of Canada",
            extra_fields={"_cloze_source": "{{c1::Ottawa}} is the capital of {{c2::Canada}}"},
            source="generated",
            source_note_id=note_id,
            source_card_ord=1,
        ),
        CardRecord(
            id=2,
            deck_id=1,
            question="Ottawa is the capital of [...]",
            answer="Ottawa is the capital of Canada",
            extra_fields={"_cloze_source": "{{c1::Ottawa}} is the capital of {{c2::Canada}}"},
            source="generated",
            source_note_id=note_id,
            source_card_ord=2,
        ),
    ]
    decks = [DeckRecord(id=1, name="Geo")]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg(cards, decks, output)
        # Two cards from same note = 1 exported note
        assert count == 1
        assert output.exists()


def test_write_apkg_suspended_tag() -> None:
    """Suspended cards get a 'suspended' tag."""
    cards = [
        CardRecord(
            id=1,
            deck_id=1,
            question="Q",
            answer="A",
            suspended=True,
        ),
    ]
    decks = [DeckRecord(id=1, name="Test")]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg(cards, decks, output)
        assert count == 1


def test_write_apkg_mixed_basic_and_cloze() -> None:
    """Basic and cloze cards in the same deck are both exported."""
    note_id = 99999
    cards = [
        CardRecord(
            id=1,
            deck_id=1,
            question="Basic Q",
            answer="Basic A",
        ),
        CardRecord(
            id=2,
            deck_id=1,
            question="[...] is big",
            answer="Earth is big",
            extra_fields={"_cloze_source": "{{c1::Earth}} is big"},
            source="generated",
            source_note_id=note_id,
            source_card_ord=1,
        ),
    ]
    decks = [DeckRecord(id=1, name="Mixed")]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test.apkg"
        count = write_apkg(cards, decks, output)
        assert count == 2  # 1 basic note + 1 cloze note
