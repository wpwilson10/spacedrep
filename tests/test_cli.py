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
            assert data["tables_created"] == 4


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


class TestErrorHandling:
    def test_card_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            _init_db(db_path)

            result = _run(["card", "suspend", "999"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stdout)
            assert data["error"] == "card_not_found"

    def test_db_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nonexistent.db"

            result = _run(["card", "next"], db_path)
            assert result.returncode == 3
            data = json.loads(result.stdout)
            assert data["error"] == "database_not_found"
