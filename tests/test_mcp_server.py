"""Tests for the MCP server tool functions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


from mcp.server.fastmcp.exceptions import ToolError

from spacedrep import core
from spacedrep.core import reset_params_loaded
from spacedrep.mcp_server import (
    add_card,
    add_cards_bulk,
    delete_card,
    export_deck,
    get_card,
    get_due_count,
    get_fsrs_status,
    get_next_card,
    get_overall_stats,
    get_session_stats,
    import_deck,
    init_database,
    list_cards,
    list_decks,
    list_tags,
    optimize_fsrs,
    preview_review,
    submit_review,
    suspend_card,
    unsuspend_card,
    update_card,
)

# All tools read DB_PATH from the module global. We monkeypatch it per test.


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp DB and point mcp_server._db_path() at it."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("SPACEDREP_DB", str(db))
    core.init_database(db)
    reset_params_loaded()
    return db


@pytest.fixture()
def card_id(tmp_db: Path) -> int:
    """Add a card and return its ID."""
    result = core.add_card(tmp_db, "What is CAP?", "Pick 2 of 3", deck="AWS", tags="distributed")
    cid: int = result["card_id"]  # type: ignore[assignment]  # runtime always int
    return cid


# ---------------------------------------------------------------------------
# Card tools
# ---------------------------------------------------------------------------


class TestAddCard:
    def test_basic(self, tmp_db: Path) -> None:
        result = add_card("Q1", "A1")
        assert result["card_id"] == 1
        assert result["deck"] == "Default"

    def test_with_deck_and_tags(self, tmp_db: Path) -> None:
        result = add_card("Q1", "A1", deck="AWS", tags="s3 storage")
        assert result["deck"] == "AWS"
        detail = core.get_card_detail(tmp_db, result["card_id"])
        assert detail.tags == "s3 storage"


class TestAddCardsBulk:
    def test_valid_json(self, tmp_db: Path) -> None:
        cards_json = json.dumps(
            [
                {"question": "Q1", "answer": "A1", "deck": "AWS"},
                {"question": "Q2", "answer": "A2"},
            ]
        )
        result = add_cards_bulk(cards_json)
        assert result["count"] == 2
        assert len(result["created"]) == 2

    def test_invalid_json(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            add_cards_bulk("not json")

    def test_empty_array(self, tmp_db: Path) -> None:
        result = add_cards_bulk("[]")
        assert result["count"] == 0


class TestGetNextCard:
    def test_no_cards_due(self, tmp_db: Path) -> None:
        result = get_next_card()
        assert result["card_id"] is None
        assert "No cards due" in result["message"]

    def test_returns_card(self, card_id: int, tmp_db: Path) -> None:
        result = get_next_card()
        assert result["card_id"] == card_id

    def test_filter_by_deck(self, card_id: int, tmp_db: Path) -> None:
        result = get_next_card(deck="AWS")
        assert result["card_id"] == card_id

    def test_filter_by_nonexistent_deck(self, card_id: int, tmp_db: Path) -> None:
        result = get_next_card(deck="Nonexistent")
        assert result["card_id"] is None

    def test_sentinel_empty_string(self, card_id: int, tmp_db: Path) -> None:
        """Empty string sentinels should behave like no filter."""
        result = get_next_card(deck="", tags="", state="")
        assert result["card_id"] == card_id


class TestListCards:
    def test_empty(self, tmp_db: Path) -> None:
        result = list_cards()
        assert result["total"] == 0

    def test_with_cards(self, card_id: int, tmp_db: Path) -> None:
        result = list_cards()
        assert result["total"] == 1

    def test_filter_by_tags(self, card_id: int, tmp_db: Path) -> None:
        result = list_cards(tags="distributed")
        assert result["total"] == 1
        result2 = list_cards(tags="nonexistent")
        assert result2["total"] == 0

    def test_pagination(self, card_id: int, tmp_db: Path) -> None:
        result = list_cards(limit=1, offset=0)
        assert len(result["cards"]) == 1


class TestGetCard:
    def test_found(self, card_id: int, tmp_db: Path) -> None:
        result = get_card(card_id)
        assert result["card_id"] == card_id
        assert result["question"] == "What is CAP?"

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            get_card(999)


class TestUpdateCard:
    def test_update_question(self, card_id: int, tmp_db: Path) -> None:
        result = update_card(card_id, question="Updated Q")
        assert result["question"] == "Updated Q"

    def test_no_fields_raises(self, card_id: int, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            update_card(card_id)

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            update_card(999, question="Q")


class TestDeleteCard:
    def test_delete(self, card_id: int, tmp_db: Path) -> None:
        result = delete_card(card_id)
        assert result["deleted"] is True

    def test_dry_run(self, card_id: int, tmp_db: Path) -> None:
        result = delete_card(card_id, dry_run=True)
        assert result["dry_run"] is True
        # Card still exists
        get_card(card_id)

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            delete_card(999)


class TestSuspendCard:
    def test_suspend(self, card_id: int, tmp_db: Path) -> None:
        result = suspend_card(card_id)
        assert result["suspended"] is True

    def test_dry_run(self, card_id: int, tmp_db: Path) -> None:
        result = suspend_card(card_id, dry_run=True)
        assert result["dry_run"] is True

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            suspend_card(999)


class TestUnsuspendCard:
    def test_unsuspend(self, card_id: int, tmp_db: Path) -> None:
        core.suspend_card(tmp_db, card_id)
        result = unsuspend_card(card_id)
        assert result["suspended"] is False

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            unsuspend_card(999)


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------


class TestSubmitReview:
    def test_good_rating(self, card_id: int, tmp_db: Path) -> None:
        result = submit_review(card_id, rating=3)
        assert result["card_id"] == card_id
        assert result["rating"] == "good"

    def test_invalid_rating(self, card_id: int, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            submit_review(card_id, rating=5)

    def test_card_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            submit_review(999, rating=3)

    def test_with_session_id(self, card_id: int, tmp_db: Path) -> None:
        result = submit_review(card_id, rating=3, session_id="test-session")
        assert result["card_id"] == card_id


class TestPreviewReview:
    def test_preview(self, card_id: int, tmp_db: Path) -> None:
        result = preview_review(card_id)
        assert result["card_id"] == card_id
        assert "previews" in result
        assert "again" in result["previews"]
        assert "easy" in result["previews"]

    def test_not_found(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            preview_review(999)


# ---------------------------------------------------------------------------
# Deck tools
# ---------------------------------------------------------------------------


class TestListTags:
    def test_empty(self, tmp_db: Path) -> None:
        result = list_tags()
        assert result["tags"] == []
        assert result["count"] == 0

    def test_with_cards(self, card_id: int, tmp_db: Path) -> None:
        result = list_tags()
        assert "distributed" in result["tags"]
        assert result["count"] >= 1


class TestListDecks:
    def test_empty(self, tmp_db: Path) -> None:
        result = list_decks()
        assert result["decks"] == []
        assert result["count"] == 0

    def test_with_deck(self, card_id: int, tmp_db: Path) -> None:
        result = list_decks()
        assert len(result["decks"]) == 1
        assert result["decks"][0]["name"] == "AWS"
        assert result["count"] == 1


class TestImportDeck:
    def test_import_roundtrip(self, card_id: int, tmp_db: Path, tmp_path: Path) -> None:
        """Export then import — verifies import_deck works end to end."""
        apkg = tmp_path / "roundtrip.apkg"
        export_deck(str(apkg))
        result = import_deck(str(apkg))
        assert result["imported"] >= 0
        assert result["updated"] >= 0
        assert "decks" in result

    def test_import_dry_run(self, card_id: int, tmp_db: Path, tmp_path: Path) -> None:
        apkg = tmp_path / "dryrun.apkg"
        export_deck(str(apkg))
        result = import_deck(str(apkg), dry_run=True)
        assert result["dry_run"] is True

    def test_import_missing_file(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError):
            import_deck("/nonexistent/file.apkg")

    def test_import_wrong_extension(self, tmp_db: Path, tmp_path: Path) -> None:
        bad_file = tmp_path / "deck.zip"
        bad_file.write_bytes(b"fake")
        with pytest.raises(ToolError, match="Expected .apkg file"):
            import_deck(str(bad_file))


class TestExportDeck:
    def test_export(self, card_id: int, tmp_db: Path, tmp_path: Path) -> None:
        out = tmp_path / "export.apkg"
        result = export_deck(str(out))
        assert result["exported"] == 1
        assert result["file"] == str(out.resolve())


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_import_path_traversal_blocked(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError, match="must not contain"):
            import_deck("../../etc/passwd.apkg")

    def test_export_path_traversal_blocked(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError, match="must not contain"):
            export_deck("../../tmp/evil.apkg")

    def test_import_sensitive_dir_blocked(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError, match="sensitive directory"):
            import_deck("/home/user/.ssh/keys.apkg")

    def test_export_sensitive_dir_blocked(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError, match="sensitive directory"):
            export_deck("/home/user/.aws/creds.apkg")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_spacedrep_error_becomes_tool_error(self, tmp_db: Path) -> None:
        """SpacedrepError should be caught and wrapped as ToolError with structured JSON."""
        with pytest.raises(ToolError) as exc_info:
            get_card(999)
        error_msg = str(exc_info.value)
        assert "card_not_found" in error_msg
        assert "999" in error_msg

    def test_tool_error_contains_suggestion(self, tmp_db: Path) -> None:
        with pytest.raises(ToolError) as exc_info:
            update_card(1)  # no fields provided
        assert "suggestion" in str(exc_info.value)

    def test_unexpected_exception_becomes_tool_error(
        self, tmp_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-SpacedrepError exceptions should be caught as internal_error."""

        def _raise(db_path: Path, card_id: int) -> None:
            _ = 1 / 0

        monkeypatch.setattr(core, "get_card_detail", _raise)
        with pytest.raises(ToolError) as exc_info:
            get_card(1)
        error_msg = str(exc_info.value)
        assert "internal_error" in error_msg


# ---------------------------------------------------------------------------
# Stats tools
# ---------------------------------------------------------------------------


class TestGetDueCount:
    def test_empty(self, tmp_db: Path) -> None:
        result = get_due_count()
        assert result["total_due"] == 0

    def test_with_due_cards(self, card_id: int, tmp_db: Path) -> None:
        result = get_due_count()
        assert result["total_due"] >= 1


class TestGetSessionStats:
    def test_empty_session(self, tmp_db: Path) -> None:
        result = get_session_stats("nonexistent")
        assert result["reviewed"] == 0

    def test_with_review(self, card_id: int, tmp_db: Path) -> None:
        submit_review(card_id, rating=3, session_id="s1")
        result = get_session_stats("s1")
        assert result["reviewed"] == 1


class TestGetOverallStats:
    def test_empty(self, tmp_db: Path) -> None:
        result = get_overall_stats()
        assert result["total_cards"] == 0

    def test_with_cards(self, card_id: int, tmp_db: Path) -> None:
        result = get_overall_stats()
        assert result["total_cards"] == 1


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


class TestInitDatabase:
    def test_init(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        db = tmp_path / "new.db"
        monkeypatch.setenv("SPACEDREP_DB", str(db))
        reset_params_loaded()
        result = init_database()
        assert result["status"] == "ok"
        assert db.exists()

    def test_idempotent(self, tmp_db: Path) -> None:
        result = init_database()
        assert result["status"] == "ok"


class TestGetFsrsStatus:
    def test_status(self, tmp_db: Path) -> None:
        result = get_fsrs_status()
        assert result["is_default"] is True
        assert "parameters" in result


class TestOptimizeFsrs:
    def test_no_reviews(self, tmp_db: Path) -> None:
        """With no reviews, optimizer returns optimized=False."""
        result = optimize_fsrs()
        assert result["review_count"] == 0
        assert result["optimized"] is False

    def test_with_reviews(self, card_id: int, tmp_db: Path) -> None:
        """With a review, optimizer runs and returns review count."""
        submit_review(card_id, rating=3)
        result = optimize_fsrs()
        assert result["review_count"] >= 1
        assert "parameters" in result

    def test_dry_run(self, card_id: int, tmp_db: Path) -> None:
        submit_review(card_id, rating=3)
        result = optimize_fsrs(dry_run=True)
        assert "Dry run" in result["message"]

    def test_optimizer_not_installed_raises_tool_error(
        self, tmp_db: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OptimizerNotInstalledError should be wrapped as ToolError."""

        def _raise(*args: object, **kwargs: object) -> None:
            raise core.OptimizerNotInstalledError()

        monkeypatch.setattr(core, "optimize_parameters", _raise)
        with pytest.raises(ToolError) as exc_info:
            optimize_fsrs()
        assert "optimizer_not_installed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Schema validation (regression guard for Claude Code compatibility)
# ---------------------------------------------------------------------------

PRIMITIVE_TYPES = {"string", "integer", "number", "boolean"}
EXPECTED_TOOL_COUNT = 23


def _get_tools() -> dict[str, object]:
    """Access registered tools from FastMCP server.

    Returns Tool objects but typed as object to avoid import issues.
    Callers use getattr/isinstance for field access.
    """

    from spacedrep.mcp_server import mcp as server

    manager = server._tool_manager  # pyright: ignore[reportPrivateUsage]
    tools = manager._tools  # pyright: ignore[reportPrivateUsage]
    result: dict[str, object] = dict(tools)
    return result


class TestSchemaValidation:
    """Verify tool schemas don't include fields that break Claude Code."""

    def test_tool_count(self) -> None:
        tools = _get_tools()
        assert len(tools) == EXPECTED_TOOL_COUNT

    def test_no_annotations(self) -> None:
        """Tool annotations silently break Claude Code (claude-code#25081)."""
        for name, tool in _get_tools().items():
            annotations = getattr(tool, "annotations", None)
            assert annotations is None, f"{name} has annotations — will break Claude Code"

    def test_all_params_are_primitives(self) -> None:
        """Objects, arrays, and anyOf as params break Claude Code serialization."""
        for name, tool in _get_tools().items():
            parameters = tool.parameters
            assert isinstance(parameters, dict)
            props = parameters.get("properties", {})
            assert isinstance(props, dict)
            for param_name_key in props:
                assert isinstance(param_name_key, str)
                param_schema = props[param_name_key]
                assert isinstance(param_schema, dict)
                param_type = param_schema.get("type")
                assert param_type in PRIMITIVE_TYPES, (
                    f"{name}.{param_name_key} has type '{param_type}' — "
                    f"must be one of {PRIMITIVE_TYPES}"
                )
                assert "anyOf" not in param_schema, f"{name}.{param_name_key} uses anyOf"
                assert "oneOf" not in param_schema, f"{name}.{param_name_key} uses oneOf"
                assert "allOf" not in param_schema, f"{name}.{param_name_key} uses allOf"

    def test_tool_names_under_limit(self) -> None:
        """Combined mcp__spacedrep__<name> must be under 64 chars."""
        prefix = "mcp__spacedrep__"
        for name in _get_tools():
            full = f"{prefix}{name}"
            assert len(full) <= 64, f"Tool name '{full}' is {len(full)} chars (max 64)"

    def test_key_params_have_descriptions(self) -> None:
        """Parameters with non-obvious semantics should have descriptions to help agents."""
        tools = _get_tools()

        def _desc(tool_name: str, param_name: str) -> str | None:
            tool = tools[tool_name]
            props = tool.parameters["properties"]
            assert isinstance(props, dict)
            schema = props[param_name]
            assert isinstance(schema, dict)
            return schema.get("description")

        # Rating semantics are critical for correct agent behavior
        assert _desc("submit_review", "rating") is not None
        # Tags format must be documented
        assert _desc("add_card", "tags") is not None
        # State filter values must be documented
        assert _desc("get_next_card", "state") is not None
        # Bulk JSON format must be documented
        assert _desc("add_cards_bulk", "cards_json") is not None
        # dry_run should be described
        assert _desc("delete_card", "dry_run") is not None
        # File paths should be described
        assert _desc("import_deck", "apkg_path") is not None
