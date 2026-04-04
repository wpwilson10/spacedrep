"""Thin wrapper around py-fsrs.

Concurrency note: _scheduler is module-level mutable state. This is safe
because FastMCP runs tool functions in an asyncio event loop (single-threaded),
so only one tool executes at a time. If the MCP server ever moves to threaded
or multiprocess execution, _scheduler must be protected with a lock or made
per-request.
"""

from fsrs import Card, Rating, ReviewLog, Scheduler, State

_scheduler = Scheduler(desired_retention=0.9, enable_fuzzing=True)
DEFAULT_PARAMS = tuple(_scheduler.parameters)


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


def preview_card(card: Card) -> list[tuple[int, Card, ReviewLog]]:
    """Preview all 4 ratings. Returns [(rating_int, updated_card, log), ...]."""
    import copy

    results: list[tuple[int, Card, ReviewLog]] = []
    for rating_int in (1, 2, 3, 4):
        card_copy = copy.deepcopy(card)
        updated, log = _scheduler.review_card(card_copy, Rating(rating_int))
        results.append((rating_int, updated, log))
    return results


def update_scheduler(params: list[float]) -> None:
    """Replace the module-level scheduler with new parameters."""
    global _scheduler
    _scheduler = Scheduler(parameters=params, desired_retention=0.9, enable_fuzzing=True)


def get_current_parameters() -> tuple[float, ...]:
    """Get the current scheduler parameters."""
    return _scheduler.parameters


def get_scheduler() -> Scheduler:
    """Get the current scheduler instance."""
    return _scheduler


def is_default_parameters() -> bool:
    """Check if the scheduler is using default parameters."""
    return _scheduler.parameters == DEFAULT_PARAMS
