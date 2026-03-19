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
