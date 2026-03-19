"""Statistics commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

stats_app = typer.Typer(name="stats", help="Review statistics")


@stats_app.command("due")
def due(
    db: Path = DB_DEFAULT,
) -> None:
    """Show count of due cards by state.

    Example:
        spacedrep stats due
    """
    try:
        result = core.get_due_count(db)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@stats_app.command("session")
def session(
    session_id: str = typer.Argument(..., help="Session ID"),
    db: Path = DB_DEFAULT,
) -> None:
    """Show stats for a specific review session.

    Example:
        spacedrep stats session session-2026-03-18
    """
    try:
        result = core.get_session_stats(db, session_id)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@stats_app.command("overall")
def overall(
    db: Path = DB_DEFAULT,
) -> None:
    """Show overall database statistics.

    Example:
        spacedrep stats overall
    """
    try:
        result = core.get_overall_stats(db)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
