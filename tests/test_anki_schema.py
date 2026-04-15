"""Tests for anki_schema module."""

import json
import sqlite3
from datetime import UTC, datetime

from fsrs import Card, State

from spacedrep.anki_schema import (
    ANKI_SCHEMA,
    BASIC_MODEL_ID,
    CLOZE_MODEL_ID,
    ColMeta,
    anki_fields_to_fsrs_card,
    basic_guid,
    cloze_guid,
    datetime_to_due,
    due_to_datetime,
    fsrs_card_to_anki_fields,
)

# ---------------------------------------------------------------------------
# DDL execution
# ---------------------------------------------------------------------------


def test_anki_schema_creates_all_tables() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ANKI_SCHEMA)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
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
    assert expected == tables
    conn.close()


def test_anki_schema_creates_indexes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(ANKI_SCHEMA)
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
    }
    assert "idx_notes_guid" in indexes
    assert "idx_cards_nid" in indexes
    assert "idx_cards_did" in indexes
    assert "idx_revlog_cid" in indexes
    conn.close()


def test_anki_schema_idempotent() -> None:
    """Running DDL twice should not error (IF NOT EXISTS)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(ANKI_SCHEMA)
    conn.executescript(ANKI_SCHEMA)
    conn.close()


# ---------------------------------------------------------------------------
# ColMeta
# ---------------------------------------------------------------------------


def test_col_meta_default_produces_valid_row() -> None:
    meta = ColMeta.default()
    row = meta.to_col_row()

    conn = sqlite3.connect(":memory:")
    conn.executescript(ANKI_SCHEMA)
    conn.execute(
        "INSERT INTO col"
        " (id, crt, mod, scm, ver, dty, usn, ls, conf, models, decks, dconf, tags)"
        " VALUES (:id, :crt, :mod, :scm, :ver, :dty, :usn, :ls,"
        " :conf, :models, :decks, :dconf, :tags)",
        row,
    )
    conn.commit()

    fetched = conn.execute("SELECT * FROM col WHERE id = 1").fetchone()
    assert fetched is not None
    conn.close()


def test_col_meta_default_has_both_models() -> None:
    meta = ColMeta.default()
    assert str(BASIC_MODEL_ID) in meta.models
    assert str(CLOZE_MODEL_ID) in meta.models


def test_col_meta_default_has_default_deck() -> None:
    meta = ColMeta.default()
    assert "1" in meta.decks
    assert meta.decks["1"]["name"] == "Default"


def test_col_meta_round_trip() -> None:
    """default -> to_col_row -> from_row should preserve data."""
    original = ColMeta.default()
    row = original.to_col_row()
    # Simulate reading back from SQLite (values are JSON strings)
    restored = ColMeta.from_row(row)
    assert restored.crt == original.crt
    assert restored.ver == original.ver
    assert set(restored.models.keys()) == set(original.models.keys())
    assert set(restored.decks.keys()) == set(original.decks.keys())


def test_col_meta_get_deck_id() -> None:
    meta = ColMeta.default()
    assert meta.get_deck_id("Default") == 1
    assert meta.get_deck_id("Nonexistent") is None


def test_col_meta_ensure_deck_existing() -> None:
    meta = ColMeta.default()
    deck_id = meta.ensure_deck("Default")
    assert deck_id == 1
    assert len(meta.decks) == 1  # no new deck added


def test_col_meta_ensure_deck_new() -> None:
    meta = ColMeta.default()
    deck_id = meta.ensure_deck("AWS")
    assert deck_id is not None
    assert deck_id != 1
    assert len(meta.decks) == 2
    assert meta.get_deck_id("AWS") == deck_id


def test_col_meta_ensure_deck_stable_id() -> None:
    """Same deck name always produces the same ID."""
    meta1 = ColMeta.default()
    meta2 = ColMeta.default()
    assert meta1.ensure_deck("Physics") == meta2.ensure_deck("Physics")


def test_col_meta_ensure_model_basic() -> None:
    meta = ColMeta.default()
    mid = meta.ensure_model("basic")
    assert mid == BASIC_MODEL_ID


def test_col_meta_ensure_model_cloze() -> None:
    meta = ColMeta.default()
    mid = meta.ensure_model("cloze")
    assert mid == CLOZE_MODEL_ID


def test_col_meta_ensure_model_adds_if_missing() -> None:
    meta = ColMeta.default()
    del meta.models[str(CLOZE_MODEL_ID)]
    assert str(CLOZE_MODEL_ID) not in meta.models
    mid = meta.ensure_model("cloze")
    assert mid == CLOZE_MODEL_ID
    assert str(CLOZE_MODEL_ID) in meta.models


# ---------------------------------------------------------------------------
# Due date helpers
# ---------------------------------------------------------------------------


class TestDueDateRoundTrips:
    """Test due date encoding/decoding for all card types."""

    CRT = 1609459200  # 2021-01-01 00:00:00 UTC

    def test_new_card_due(self) -> None:
        """New cards (type=0): due is a position, not a date."""
        dt = due_to_datetime(42, 0, self.CRT)
        assert dt == datetime.fromtimestamp(0, tz=UTC)

    def test_new_card_datetime_to_due(self) -> None:
        """New card due conversion returns 0 (caller sets position)."""
        dt = datetime.now(tz=UTC)
        assert datetime_to_due(dt, 0, self.CRT) == 0

    def test_learning_card_round_trip(self) -> None:
        """Learning (type=1): due is unix timestamp in seconds."""
        ts = 1700000000
        dt = due_to_datetime(ts, 1, self.CRT)
        assert int(dt.timestamp()) == ts
        assert datetime_to_due(dt, 1, self.CRT) == ts

    def test_relearning_card_round_trip(self) -> None:
        """Relearning (type=3): same as learning."""
        ts = 1700000000
        dt = due_to_datetime(ts, 3, self.CRT)
        assert int(dt.timestamp()) == ts
        assert datetime_to_due(dt, 3, self.CRT) == ts

    def test_review_card_round_trip(self) -> None:
        """Review (type=2): due is days since crt."""
        days = 100
        dt = due_to_datetime(days, 2, self.CRT)
        expected_ts = self.CRT + days * 86400
        assert int(dt.timestamp()) == expected_ts
        assert datetime_to_due(dt, 2, self.CRT) == days

    def test_review_card_due_zero(self) -> None:
        """Review card with due=0 means same day as collection creation."""
        dt = due_to_datetime(0, 2, self.CRT)
        assert int(dt.timestamp()) == self.CRT

    def test_review_card_same_day(self) -> None:
        """Due date on the creation day produces due=0."""
        crt_dt = datetime.fromtimestamp(self.CRT, tz=UTC)
        assert datetime_to_due(crt_dt, 2, self.CRT) == 0


# ---------------------------------------------------------------------------
# FSRS conversion
# ---------------------------------------------------------------------------


class TestFsrsConversion:
    """Test FSRS <-> Anki field conversion."""

    CRT = 1609459200

    def test_new_card_to_anki(self) -> None:
        """A fresh FSRS Card maps to type=0, queue=0."""
        card = Card()
        fields = fsrs_card_to_anki_fields(card, self.CRT)
        assert fields["type"] == 0
        assert fields["queue"] == 0
        assert fields["factor"] == 2500
        assert fields["left"] == 0
        assert fields["odue"] == 0
        assert fields["odid"] == 0
        assert fields["flags"] == 0
        assert fields["usn"] == -1

    def test_review_card_round_trip(self) -> None:
        """Create an FSRS Card in review state, convert to Anki, convert back."""
        card = Card()
        card.state = State.Review
        card.stability = 15.5
        card.difficulty = 6.3
        card.due = datetime(2025, 6, 15, tzinfo=UTC)
        card.last_review = datetime(2025, 6, 1, tzinfo=UTC)
        card.step = 0

        anki = fsrs_card_to_anki_fields(card, self.CRT)
        assert anki["type"] == 2
        assert anki["queue"] == 2
        assert anki["ivl"] == 16  # round(15.5)

        # Round-trip back
        restored = anki_fields_to_fsrs_card(anki, self.CRT)
        assert restored.state == State.Review
        assert restored.stability is not None
        assert abs(restored.stability - 15.5) < 0.01
        assert restored.difficulty is not None
        assert abs(restored.difficulty - 6.3) < 0.01
        assert restored.last_review is not None
        assert int(restored.last_review.timestamp()) == int(card.last_review.timestamp())

    def test_learning_card_round_trip(self) -> None:
        card = Card()
        card.state = State.Learning
        card.stability = 0.5
        card.difficulty = 7.0
        card.due = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        card.last_review = datetime(2025, 6, 1, 11, 50, 0, tzinfo=UTC)

        anki = fsrs_card_to_anki_fields(card, self.CRT)
        assert anki["type"] == 1
        assert anki["queue"] == 1

        restored = anki_fields_to_fsrs_card(anki, self.CRT)
        assert restored.state == State.Learning

    def test_relearning_card_round_trip(self) -> None:
        card = Card()
        card.state = State.Relearning
        card.stability = 3.0
        card.difficulty = 8.0
        card.due = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        card.last_review = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)

        anki = fsrs_card_to_anki_fields(card, self.CRT)
        assert anki["type"] == 3
        assert anki["queue"] == 1  # relearning uses learning queue

        restored = anki_fields_to_fsrs_card(anki, self.CRT)
        assert restored.state == State.Relearning

    def test_sm2_estimation_from_factor(self) -> None:
        """SM-2 card (no data.s/d) gets estimated FSRS values."""
        row: dict[str, object] = {
            "type": 2,
            "due": 100,
            "ivl": 10,
            "factor": 2500,
            "data": "",
        }
        card = anki_fields_to_fsrs_card(row, self.CRT)
        assert card.stability is not None
        assert card.stability == 10.0  # stability ~ ivl
        assert card.difficulty is not None
        assert abs(card.difficulty - 5.0) < 0.01  # 10 - 2500/500

    def test_sm2_estimation_clamps_difficulty(self) -> None:
        """Difficulty is clamped to [1.0, 10.0]."""
        # Very high factor -> low difficulty, should clamp to 1.0
        row: dict[str, object] = {
            "type": 2,
            "due": 100,
            "ivl": 30,
            "factor": 5000,
            "data": "",
        }
        card = anki_fields_to_fsrs_card(row, self.CRT)
        assert card.difficulty == 1.0  # clamped

    def test_sm2_last_review_derived(self) -> None:
        """SM-2 review card derives last_review from due - ivl."""
        row: dict[str, object] = {
            "type": 2,
            "due": 110,  # days since crt
            "ivl": 10,
            "factor": 2500,
            "data": "",
        }
        card = anki_fields_to_fsrs_card(row, self.CRT)
        assert card.last_review is not None
        # last_review = crt + (110 - 10) * 86400 = crt + 100 days
        expected_ts = self.CRT + 100 * 86400
        assert int(card.last_review.timestamp()) == expected_ts

    def test_all_card_columns_present(self) -> None:
        """fsrs_card_to_anki_fields returns all required columns."""
        card = Card()
        fields = fsrs_card_to_anki_fields(card, self.CRT)
        required = {
            "type",
            "queue",
            "due",
            "ivl",
            "factor",
            "reps",
            "lapses",
            "left",
            "odue",
            "odid",
            "flags",
            "data",
            "mod",
            "usn",
        }
        assert required == set(fields.keys())

    def test_data_json_has_lrt_when_reviewed(self) -> None:
        """Reviewed cards include lrt (last review time) in data JSON."""
        card = Card()
        card.state = State.Review
        card.stability = 10.0
        card.difficulty = 5.0
        card.due = datetime(2025, 7, 1, tzinfo=UTC)
        card.last_review = datetime(2025, 6, 15, tzinfo=UTC)

        fields = fsrs_card_to_anki_fields(card, self.CRT)
        data = json.loads(str(fields["data"]))
        assert "s" in data
        assert "d" in data
        assert "lrt" in data
        assert data["lrt"] == int(card.last_review.timestamp())


# ---------------------------------------------------------------------------
# GUID helpers
# ---------------------------------------------------------------------------


class TestGuidGeneration:
    def test_basic_guid_deterministic(self) -> None:
        g1 = basic_guid("What is X?", "Default")
        g2 = basic_guid("What is X?", "Default")
        assert g1 == g2
        assert len(g1) == 10

    def test_basic_guid_different_inputs(self) -> None:
        g1 = basic_guid("What is X?", "Default")
        g2 = basic_guid("What is Y?", "Default")
        g3 = basic_guid("What is X?", "Science")
        assert g1 != g2
        assert g1 != g3

    def test_cloze_guid_deterministic(self) -> None:
        text = "{{c1::Ottawa}} is the capital of {{c2::Canada}}"
        g1 = cloze_guid(text)
        g2 = cloze_guid(text)
        assert g1 == g2
        assert len(g1) == 10

    def test_cloze_guid_different_inputs(self) -> None:
        g1 = cloze_guid("{{c1::Ottawa}} is the capital of Canada")
        g2 = cloze_guid("{{c1::Paris}} is the capital of France")
        assert g1 != g2

    def test_guids_are_hex(self) -> None:
        g = basic_guid("test", "deck")
        assert all(c in "0123456789abcdef" for c in g)
