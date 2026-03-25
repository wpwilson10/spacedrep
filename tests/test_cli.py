"""CLI integration tests using subprocess."""

import json
import subprocess
import tempfile
from pathlib import Path

_PROJECT = str(Path(__file__).parents[1])


def _run(args: list[str], db_path: Path) -> subprocess.CompletedProcess[str]:
    """Run spacedrep CLI command."""
    cmd = ["uv", "run", "--project", _PROJECT, "spacedrep", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _run_bare(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run spacedrep CLI without --db."""
    cmd = ["uv", "run", "--project", _PROJECT, "spacedrep", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _init_db(db_path: Path) -> None:
    result = _run(["db", "init"], db_path)
    assert result.returncode == 0


def _add_card(
    db_path: Path, question: str, answer: str, deck: str = "Default", tags: str = ""
) -> None:
    args = ["card", "add", question, answer, "--deck", deck]
    if tags:
        args.extend(["--tags", tags])
    result = _run(args, db_path)
    assert result.returncode == 0


class TestHelp:
    def test_main_help(self) -> None:
        result = _run_bare(["--help"])
        assert result.returncode == 0
        assert "Agent-first" in result.stdout

    def test_card_help(self) -> None:
        result = _run_bare(["card", "--help"])
        assert result.returncode == 0
        assert "next" in result.stdout


class TestDbInit:
    def test_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            result = _run(["db", "init"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["status"] == "ok"
            assert data["tables_created"] == 5


class TestCardCommands:
    def test_add_and_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "add", "What is X?", "X is Y.", "--deck", "Test"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] == 1

            result = _run(["card", "next"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["question"] == "What is X?"

    def test_no_cards_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "next"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] is None

    def test_suspend_unsuspend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _run(["card", "add", "Q", "A"], db_path)

            result = _run(["card", "suspend", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["suspended"] is True

            result = _run(["card", "unsuspend", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["suspended"] is False


class TestCardList:
    def test_list_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "list"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 0
            assert data["cards"] == []

    def test_list_with_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS", tags="s3")
            _add_card(db_path, "Q2", "A2", deck="DSA", tags="trees")

            result = _run(["card", "list"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 2
            assert len(data["cards"]) == 2

    def test_list_filter_by_deck(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS")
            _add_card(db_path, "Q2", "A2", deck="DSA")

            result = _run(["card", "list", "--deck", "AWS"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 1
            assert data["cards"][0]["deck"] == "AWS"

    def test_list_filter_by_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", tags="s3,storage")
            _add_card(db_path, "Q2", "A2", tags="compute")

            result = _run(["card", "list", "--tags", "s3"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 1

    def test_list_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")
            _add_card(db_path, "Q2", "A2")
            _add_card(db_path, "Q3", "A3")

            result = _run(["card", "list", "--limit", "2", "--offset", "0"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert len(data["cards"]) == 2
            assert data["total"] == 3


class TestCardGet:
    def test_get_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS", tags="s3")

            result = _run(["card", "get", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] == 1
            assert data["question"] == "Q1"
            assert data["answer"] == "A1"
            assert data["deck"] == "AWS"
            assert data["state"] == "new"

    def test_get_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "get", "999"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"


class TestCardDelete:
    def test_delete_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "delete", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] == 1
            assert data["deleted"] is True

    def test_delete_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "delete", "999"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"


class TestCardUpdate:
    def test_update_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "update", "1", "--question", "Updated Q"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["question"] == "Updated Q"
            assert data["answer"] == "A1"

    def test_update_deck_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS")

            result = _run(["card", "update", "1", "--deck", "DSA"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["deck"] == "DSA"

    def test_update_no_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "update", "1"], db_path)
            assert result.returncode == 2
            data = json.loads(result.stderr)
            assert data["error"] == "no_fields"
            assert "suggestion" in data

    def test_update_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "update", "999", "--question", "nope"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"


class TestCardNextFilters:
    def test_next_with_deck(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS")
            _add_card(db_path, "Q2", "A2", deck="DSA")

            result = _run(["card", "next", "--deck", "DSA"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["deck"] == "DSA"

    def test_next_with_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", tags="s3,storage")
            _add_card(db_path, "Q2", "A2", tags="compute")

            result = _run(["card", "next", "--tags", "compute"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert "compute" in data["tags"]


class TestReview:
    def test_submit_good(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _run(["card", "add", "Q", "A"], db_path)

            result = _run(["review", "submit", "1", "good"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["rating"] == "good"

    def test_submit_by_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _run(["card", "add", "Q", "A"], db_path)

            result = _run(["review", "submit", "1", "3"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["rating"] == "good"


class TestStats:
    def test_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _run(["card", "add", "Q", "A"], db_path)

            result = _run(["stats", "due"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total_due"] == 1

    def test_overall(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["stats", "overall"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total_cards"] == 0


class TestDeck:
    def test_list_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["deck", "list"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data == []

    def test_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _run(["card", "add", "Q", "A", "--deck", "Test"], db_path)

            export_path = Path(tmpdir) / "export.apkg"
            result = _run(["deck", "export", str(export_path)], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["exported"] == 1
            assert export_path.exists()


class TestBulkAdd:
    def test_add_bulk_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            input_json = json.dumps(
                [
                    {"question": "Q1", "answer": "A1", "deck": "Test"},
                    {"question": "Q2", "answer": "A2"},
                ]
            )
            cmd = [
                "uv",
                "run",
                "--project",
                _PROJECT,
                "spacedrep",
                "card",
                "add-bulk",
                "--db",
                str(db_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, input=input_json
            )
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["count"] == 2
            assert len(data["created"]) == 2

    def test_add_bulk_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            cmd = [
                "uv",
                "run",
                "--project",
                _PROJECT,
                "spacedrep",
                "card",
                "add-bulk",
                "--db",
                str(db_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, input="not json"
            )
            assert result.returncode == 2
            data = json.loads(result.stderr)
            assert data["error"] == "bulk_input_error"


class TestLeech:
    def test_leech_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "list", "--leeches"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 0  # No leeches


class TestReviewPreview:
    def test_preview_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["review", "preview", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] == 1
            assert "again" in data["previews"]
            assert "good" in data["previews"]
            assert len(data["previews"]) == 4

    def test_preview_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["review", "preview", "999"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"


class TestFsrs:
    def test_fsrs_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["fsrs", "status"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["is_default"] is True
            assert data["review_count"] == 0
            assert data["can_optimize"] is False

    def test_fsrs_optimize_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["fsrs", "optimize"], db_path)
            # Will fail with optimizer not installed (exit code 1)
            # or succeed with "no review logs" message
            if result.returncode == 0:
                data = json.loads(result.stdout)
                assert data["optimized"] is False
            else:
                data = json.loads(result.stderr)
                assert data["error"] == "optimizer_not_installed"


class TestErrorHandling:
    def test_card_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "suspend", "999"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"

    def test_db_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nonexistent.db"

            result = _run(["card", "next"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "database_not_found"


class TestQuiet:
    def test_card_add_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "add", "Q1", "A1", "-q"], db_path)
            assert result.returncode == 0
            assert result.stdout.strip() == "1"

    def test_card_list_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")
            _add_card(db_path, "Q2", "A2")
            _add_card(db_path, "Q3", "A3")

            result = _run(["card", "list", "-q"], db_path)
            assert result.returncode == 0
            lines = result.stdout.strip().split("\n")
            assert lines == ["1", "2", "3"]

    def test_card_next_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "next", "-q"], db_path)
            assert result.returncode == 0
            assert result.stdout.strip() == "1"

    def test_card_next_quiet_no_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "next", "-q"], db_path)
            assert result.returncode == 0
            assert result.stdout.strip() == ""

    def test_deck_list_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1", deck="AWS")
            _add_card(db_path, "Q2", "A2", deck="DSA")

            result = _run(["deck", "list", "-q"], db_path)
            assert result.returncode == 0
            lines = sorted(result.stdout.strip().split("\n"))
            assert lines == ["AWS", "DSA"]

    def test_review_submit_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["review", "submit", "1", "good", "-q"], db_path)
            assert result.returncode == 0
            assert result.stdout.strip() == "1"


class TestDryRun:
    def test_delete_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "delete", "1", "--dry-run"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["dry_run"] is True
            assert data["card_id"] == 1
            assert data["action"] == "delete"

            # Card should still exist
            result = _run(["card", "get", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["card_id"] == 1

    def test_suspend_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            result = _run(["card", "suspend", "1", "--dry-run"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["dry_run"] is True
            assert data["current_suspended"] is False

            # Card should NOT be suspended
            result = _run(["card", "get", "1"], db_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["suspended"] is False

    def test_import_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)
            _add_card(db_path, "Q1", "A1")

            # Export first to get an .apkg
            apkg_path = Path(tmpdir) / "export.apkg"
            result = _run(["deck", "export", str(apkg_path)], db_path)
            assert result.returncode == 0

            # Dry-run import into a fresh DB
            db2_path = Path(tmpdir) / "test2.db"
            _run(["db", "init"], db2_path)

            result = _run(["deck", "import", str(apkg_path), "--dry-run"], db2_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["dry_run"] is True
            assert data["imported"] == 1

            # DB should still be empty
            result = _run(["card", "list"], db2_path)
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["total"] == 0

    def test_delete_dry_run_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "delete", "999", "--dry-run"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stderr)
            assert data["error"] == "card_not_found"
