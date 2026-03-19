"""Database management commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json

db_app = typer.Typer(name="db", help="Database management")


@db_app.command("init")
def init(
    db: Path = DB_DEFAULT,
) -> None:
    """Initialize the database. Creates tables if they don't exist.

    Example:
        spacedrep db init
        spacedrep db init --db ~/study/reviews.db
    """
    try:
        result = core.init_database(db)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None
