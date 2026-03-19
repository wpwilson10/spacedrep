"""Review commands."""

from pathlib import Path

import typer

from spacedrep import core
from spacedrep.cli import DB_DEFAULT, output_error, output_json
from spacedrep.models import ReviewInput

review_app = typer.Typer(name="review", help="Review operations")

_RATING_MAP = {"again": 1, "hard": 2, "good": 3, "easy": 4}


@review_app.command("submit")
def submit(
    card_id: int = typer.Argument(..., help="Card ID to review"),
    rating: str = typer.Argument(..., help="Rating: again/hard/good/easy or 1-4"),
    answer: str | None = typer.Option(None, "--answer", help="User's answer text"),
    feedback: str | None = typer.Option(None, "--feedback", help="Feedback text"),
    session: str | None = typer.Option(None, "--session", help="Session ID"),
    db: Path = DB_DEFAULT,
) -> None:
    """Submit a review for a card.

    Example:
        spacedrep review submit 42 good --answer "CAP means pick 2 of 3"
        spacedrep review submit 42 3 --session session-2026-03-18
    """
    rating_int = _parse_rating(rating)
    if rating_int is None:
        output_error(core.InvalidRatingError(rating))
        raise typer.Exit(code=2)

    review_input = ReviewInput(
        card_id=card_id,
        rating=rating_int,
        user_answer=answer,
        feedback=feedback,
        session_id=session,
    )

    try:
        result = core.submit_review(db, review_input)
        output_json(result)
    except core.SpacedrepError as e:
        output_error(e)
        raise typer.Exit(code=e.exit_code) from None


def _parse_rating(rating: str) -> int | None:
    """Parse a rating string to int. Accepts names or numbers."""
    if rating.lower() in _RATING_MAP:
        return _RATING_MAP[rating.lower()]
    try:
        val = int(rating)
        if 1 <= val <= 4:
            return val
    except ValueError:
        pass
    return None
