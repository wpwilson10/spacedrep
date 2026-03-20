"""Pydantic data models used across the codebase."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CardSource = Literal["apkg", "manual", "generated"]


class DeckRecord(BaseModel):
    id: int | None = None
    name: str
    source_id: int | None = None
    created_at: datetime | None = None


class CardRecord(BaseModel):
    id: int | None = None
    deck_id: int
    question: str
    answer: str
    extra_fields: dict[str, str] = {}
    tags: str = ""
    source: CardSource = "manual"
    source_note_id: int | None = None
    source_note_guid: str | None = None
    created_at: datetime | None = None
    suspended: bool = False


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


class ReviewResult(BaseModel):
    """Returned by review submit."""

    card_id: int
    rating: str
    new_state: str
    new_due: str
    stability: float
    difficulty: float
    interval_days: float


class DueCount(BaseModel):
    total_due: int
    learning: int
    review: int
    new: int


class DeckInfo(BaseModel):
    name: str
    card_count: int
    due_count: int


class ImportResult(BaseModel):
    imported: int
    updated: int
    deck: str
    fields: list[str]
    question_field: str
    answer_field: str


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
    created_at: str | None = None
    # FSRS state
    state: str
    due: str
    stability: float
    difficulty: float
    retrievability: float
    last_review: str | None = None
    review_count: int = 0


class CardListResult(BaseModel):
    """Paginated card list response."""

    cards: list[CardSummary]
    total: int
    limit: int
    offset: int
