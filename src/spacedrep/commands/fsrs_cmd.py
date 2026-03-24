"""FSRS scheduler management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

fsrs_app = typer.Typer(name="fsrs", help="FSRS scheduler management")


@fsrs_app.command("optimize")
def optimize(
    reschedule: bool = typer.Option(False, help="Reschedule all cards with new params"),
    db: Path = DB_DEFAULT,
) -> None:
    """Optimize FSRS parameters from review history.

    Requires 512+ reviews for meaningful optimization.
    Install the optimizer extra: pip install spacedrep[optimizer]

    Example:
        spacedrep fsrs optimize
        spacedrep fsrs optimize --reschedule
    """
    try:
        result = core.optimize_parameters(db, reschedule=reschedule)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


@fsrs_app.command("status")
def status(
    db: Path = DB_DEFAULT,
) -> None:
    """Show current FSRS scheduler status.

    Example:
        spacedrep fsrs status
    """
    try:
        result = core.get_fsrs_status(db)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
