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

_CLOZE_MODEL_ID = int(hashlib.sha256(b"spacedrep-cloze").hexdigest()[:8], 16)

_CLOZE_MODEL = genanki.Model(
    model_id=_CLOZE_MODEL_ID,
    name="spacedrep-cloze",
    model_type=1,
    fields=[
        {"name": "Text"},
        {"name": "Back Extra"},
    ],
    templates=[
        {
            "name": "Cloze",
            "qfmt": "{{cloze:Text}}",
            "afmt": "{{cloze:Text}}<br>{{Back Extra}}",
        },
    ],
)


def _deck_id_from_name(name: str) -> int:
    """Generate a stable deck ID from the deck name."""
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


def _card_tags(card: CardRecord) -> list[str]:
    """Build tag list for a card, adding special tags as needed."""
    tags = card.tags.split() if card.tags else []
    if card.source == "generated":
        tags.append("ai_generated")
    if card.suspended:
        tags.append("suspended")
    return tags


def write_apkg(
    cards: list[CardRecord],
    decks: list[DeckRecord],
    output_path: Path,
) -> int:
    """Write cards to an .apkg file. Returns count of exported cards.

    Cloze cards (those with _cloze_source in extra_fields) are grouped by
    source_note_id and exported as proper cloze notes. Basic cards export
    as before. Suspended cards get a 'suspended' tag.
    """

    # Separate cloze and basic cards
    cloze_groups: dict[tuple[int, int], list[CardRecord]] = {}
    basic_cards: list[CardRecord] = []
    for card in cards:
        if card.source_note_id and "_cloze_source" in card.extra_fields:
            key = (card.deck_id, card.source_note_id)
            cloze_groups.setdefault(key, []).append(card)
        else:
            basic_cards.append(card)

    # Build deck name lookup
    deck_name_lookup: dict[int, str] = {}
    for d in decks:
        if d.id is not None:
            deck_name_lookup[d.id] = d.name

    # Collect notes per deck
    deck_notes: dict[int, list[genanki.Note]] = {}

    # Basic cards: one note per card
    for card in basic_cards:
        note = genanki.Note(
            model=_MODEL,
            fields=[html.escape(card.question), html.escape(card.answer)],
            tags=_card_tags(card),
            guid=card.source_note_guid if card.source_note_guid else None,
        )
        deck_notes.setdefault(card.deck_id, []).append(note)

    # Cloze groups: one note per source_note_id
    for (deck_id, _note_id), group in cloze_groups.items():
        first = group[0]
        cloze_text = first.extra_fields["_cloze_source"]
        note = genanki.Note(
            model=_CLOZE_MODEL,
            fields=[cloze_text, ""],
            tags=_card_tags(first),
            guid=first.source_note_guid if first.source_note_guid else None,
        )
        deck_notes.setdefault(deck_id, []).append(note)

    genanki_decks: list[genanki.Deck] = []
    total = 0

    for deck_id, notes in deck_notes.items():
        deck_name = deck_name_lookup.get(deck_id, "Default")
        gk_deck = genanki.Deck(
            deck_id=_deck_id_from_name(deck_name),
            name=deck_name,
        )
        for note in notes:
            gk_deck.add_note(note)  # type: ignore[no-untyped-call]  # genanki untyped
            total += 1
        genanki_decks.append(gk_deck)

    package = genanki.Package(genanki_decks)
    package.write_to_file(str(output_path))  # type: ignore[no-untyped-call]  # genanki untyped
    return total
