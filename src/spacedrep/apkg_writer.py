"""Generate .apkg files via genanki."""

import hashlib
import html
from pathlib import Path

import genanki

from spacedrep.models import CardRecord, DeckRecord

# Stable model ID derived from hash of "spacedrep"
_MODEL_ID = int(hashlib.sha256(b"spacedrep").hexdigest()[:8], 16)

_MODEL = genanki.Model(
    model_id=_MODEL_ID,
    name="spacedrep",
    fields=[
        {"name": "Question"},
        {"name": "Answer"},
    ],
    templates=[
        {
            "name": "Card 1",
            "qfmt": "{{Question}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Answer}}',
        },
    ],
)


def _deck_id_from_name(name: str) -> int:
    """Generate a stable deck ID from the deck name."""
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


def write_apkg(
    cards: list[CardRecord],
    decks: list[DeckRecord],
    output_path: Path,
) -> int:
    """Write cards to an .apkg file. Returns count of exported cards.

    Groups cards by deck. Uses source_note_guid as guid if available.
    """

    # Group cards by deck_id
    cards_by_deck: dict[int, list[CardRecord]] = {}
    for card in cards:
        cards_by_deck.setdefault(card.deck_id, []).append(card)

    # Build deck name lookup
    deck_name_lookup: dict[int, str] = {}
    for d in decks:
        if d.id is not None:
            deck_name_lookup[d.id] = d.name

    genanki_decks: list[genanki.Deck] = []
    total = 0

    for deck_id, deck_cards in cards_by_deck.items():
        deck_name = deck_name_lookup.get(deck_id, "Default")
        gk_deck = genanki.Deck(
            deck_id=_deck_id_from_name(deck_name),
            name=deck_name,
        )

        for card in deck_cards:
            tags = [t.strip() for t in card.tags.split(",") if t.strip()] if card.tags else []
            if card.source == "generated":
                tags.append("ai_generated")

            guid = None
            if card.source_note_guid:
                guid = card.source_note_guid

            note = genanki.Note(
                model=_MODEL,
                fields=[html.escape(card.question), html.escape(card.answer)],
                tags=tags,
                guid=guid,
            )
            gk_deck.add_note(note)  # type: ignore[no-untyped-call]  # genanki untyped
            total += 1

        genanki_decks.append(gk_deck)

    package = genanki.Package(genanki_decks)
    package.write_to_file(str(output_path))  # type: ignore[no-untyped-call]  # genanki untyped
    return total
