"""Pydantic data models used across the codebase."""

from typing import Literal

from pydantic import BaseModel, model_validator

CardSource = Literal["apkg", "manual", "generated"]


class ReviewInput(BaseModel):
    card_id: int
    rating: int  # 1-4
    user_answer: str | None = None
    feedback: str | None = None
    session_id: str | None = None


class CardDue(BaseModel):
    """Returned by card next."""

    card_id: int
    question: str
    answer: str
    deck: str
    tags: str
    state: str  # "new" | "learning" | "review" | "relearning"
    retrievability: float
    extra_fields: dict[str, str] = {}
    recent_reviews: list["ReviewLogEntry"] = []
    due_remaining: int = 0


class ReviewResult(BaseModel):
    """Returned by review submit."""

    card_id: int
    rating: str
    new_state: str
    new_due: str
    stability: float
    difficulty: float
    interval_days: float
    is_leech: bool = False
    siblings_buried: list[int] = []


class DueCount(BaseModel):
    total_due: int
    learning: int
    review: int
    new: int


class DeckInfo(BaseModel):
    name: str
    card_count: int
    due_count: int


class SessionStats(BaseModel):
    reviewed: int
    again: int
    hard: int
    good: int
    easy: int
    accuracy: float  # (good + easy) / reviewed


class OverallStats(BaseModel):
    total_cards: int
    due_now: int
    learning: int
    review: int
    mature: int  # stability > 21 days
    avg_retention: float


class CardSummary(BaseModel):
    """Compact card info for list results."""

    card_id: int
    question: str  # truncated to 100 chars
    deck: str
    tags: str
    state: str
    due: str
    suspended: bool
    buried: bool = False
    lapse_count: int = 0


class CardDetail(BaseModel):
    """Full card data with FSRS scheduling state."""

    card_id: int
    question: str
    answer: str
    deck: str
    tags: str
    extra_fields: dict[str, str] = {}
    source: CardSource = "manual"
    suspended: bool = False
    buried_until: str | None = None
    created_at: str | None = None
    # FSRS state
    state: str
    due: str
    stability: float
    difficulty: float
    retrievability: float
    last_review: str | None = None
    review_count: int = 0
    lapse_count: int = 0
    recent_reviews: list["ReviewLogEntry"] = []


class CardListResult(BaseModel):
    """Paginated card list response."""

    cards: list[CardSummary]
    total: int
    limit: int
    offset: int


class BulkCardInput(BaseModel):
    """Input for bulk card creation."""

    question: str
    answer: str = ""
    deck: str = "Default"
    tags: str = ""
    type: Literal["basic", "cloze", "reversed"] = "basic"

    @model_validator(mode="after")
    def _validate_fields(self) -> "BulkCardInput":
        if not self.question.strip():
            msg = "Question cannot be empty or whitespace-only"
            raise ValueError(msg)
        if self.type in ("basic", "reversed") and not self.answer.strip():
            msg = f"{self.type.capitalize()} cards require a non-empty answer"
            raise ValueError(msg)
        if not self.deck.strip():
            msg = "Deck name cannot be empty or whitespace-only"
            raise ValueError(msg)
        return self


class ClozeAddResult(BaseModel):
    """Result of cloze note creation."""

    note_id: int
    card_ids: list[int]
    card_count: int
    deck: str


class ReversedAddResult(BaseModel):
    """Result of reversed card pair creation (1 note + 2 cards)."""

    note_id: int
    card_ids: list[int]
    card_count: int
    deck: str


class BulkAddResult(BaseModel):
    """Result of bulk card creation."""

    created: list[int]
    total: int


class RatingPreview(BaseModel):
    """Preview of what a single rating would produce."""

    rating: str
    new_state: str
    new_due: str
    stability: float
    difficulty: float
    interval_days: float


class ReviewPreview(BaseModel):
    """Preview of all 4 ratings for a card."""

    card_id: int
    current_state: str
    previews: dict[str, RatingPreview]


class ReviewLogEntry(BaseModel):
    """A single review log entry."""

    card_id: int
    rating: int
    rating_name: str
    reviewed_at: str
    user_answer: str | None = None
    feedback: str | None = None
    session_id: str | None = None


class ReviewHistory(BaseModel):
    """Review history for a card."""

    card_id: int
    reviews: list[ReviewLogEntry]
    total: int


class OptimizeResult(BaseModel):
    """Result of FSRS parameter optimization."""

    optimized: bool
    parameters: list[float]
    review_count: int
    rescheduled: int
    message: str


class FsrsStatus(BaseModel):
    """Current FSRS scheduler status."""

    parameters: list[float]
    is_default: bool
    review_count: int
    min_reviews_needed: int
    can_optimize: bool


class OpenResult(BaseModel):
    """Result of opening an .apkg file as a working database."""

    db_path: str
    card_count: int
    deck_count: int
    decks: list[str]


class SaveResult(BaseModel):
    """Result of saving the working database as an .apkg file."""

    output_path: str
    card_count: int
