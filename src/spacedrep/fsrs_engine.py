"""Thin wrapper around py-fsrs."""

from fsrs import Card, Rating, ReviewLog, Scheduler, State

_scheduler = Scheduler(desired_retention=0.9, enable_fuzzing=True)


def create_new_card() -> Card:
    """Create a fresh FSRS card, due immediately."""
    return Card()


def review_card(card: Card, rating: int) -> tuple[Card, ReviewLog]:
    """Review a card with the given rating (1-4). Returns (updated_card, review_log)."""
    fsrs_rating = Rating(rating)
    return _scheduler.review_card(card, fsrs_rating)


def get_retrievability(card: Card) -> float:
    """Get the current probability of recall for a card."""
    return _scheduler.get_card_retrievability(card)


def serialize_card(card: Card) -> str:
    """Serialize a Card to a JSON string."""
    return card.to_json()


def deserialize_card(json_str: str) -> Card:
    """Deserialize a JSON string to a Card."""
    return Card.from_json(json_str)


def state_name(state: State, last_review: str | None = None) -> str:
    """Convert State enum to human-readable string.

    A card in State.Learning with no last_review is "new".
    """
    if state == State.Learning and last_review is None:
        return "new"
    names: dict[State, str] = {
        State.Learning: "learning",
        State.Review: "review",
        State.Relearning: "relearning",
    }
    return names.get(state, "learning")


def rating_name(rating: int) -> str:
    """Convert rating int (1-4) to name."""
    names = {1: "again", 2: "hard", 3: "good", 4: "easy"}
    return names.get(rating, "unknown")
