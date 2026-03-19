"""Parse .apkg files (ZIP'd SQLite) into card records."""

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from spacedrep.models import CardRecord, DeckRecord

QUESTION_FIELD_NAMES = {"front", "question", "prompt", "q"}
ANSWER_FIELD_NAMES = {"back", "answer", "implementation", "a", "response"}


def read_apkg(
    apkg_path: Path,
    question_field: str | None = None,
    answer_field: str | None = None,
) -> tuple[list[DeckRecord], list[CardRecord], dict[str, list[str] | str], dict[int, str]]:
    """Read an .apkg file and return (decks, cards, field_info, note_deck_map).

    field_info contains: fields (list of field names), question_field, answer_field.
    note_deck_map maps source_note_id to deck name.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        with zipfile.ZipFile(apkg_path, "r") as zf:
            zf.extractall(tmppath)

        # Find the SQLite database
        db_file = tmppath / "collection.anki21"
        if not db_file.exists():
            db_file = tmppath / "collection.anki2"
        if not db_file.exists():
            msg = f"No collection database found in {apkg_path}"
            raise ValueError(msg)

        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row

        try:
            # Parse models and decks from col table
            col_row = conn.execute("SELECT models, decks FROM col").fetchone()
            models_json = json.loads(col_row["models"])
            decks_json = json.loads(col_row["decks"])

            # Build model field mapping: {model_id: [field_names]}
            model_fields: dict[str, list[str]] = {}
            for mid, model in models_json.items():
                model_fields[mid] = [f["name"] for f in model["flds"]]

            # Build deck name mapping: {deck_id: deck_name}
            deck_names: dict[str, str] = {}
            for did, deck_data in decks_json.items():
                deck_names[did] = deck_data["name"]

            # Get card-to-deck mapping
            card_deck_map: dict[int, str] = {}
            for row in conn.execute("SELECT nid, did FROM cards").fetchall():
                card_deck_map[row["nid"]] = str(row["did"])

            # Process notes
            notes = conn.execute("SELECT id, mid, flds, guid, tags FROM notes").fetchall()

            all_field_names: list[str] = []
            q_field = ""
            a_field = ""
            decks: list[DeckRecord] = []
            cards: list[CardRecord] = []
            note_deck_map: dict[int, str] = {}
            seen_decks: set[str] = set()

            for note in notes:
                mid = str(note["mid"])
                fields = note["flds"].split("\x1f")
                field_names = model_fields.get(mid, [])

                if not all_field_names and field_names:
                    all_field_names = field_names
                    qi, ai = detect_field_mapping(field_names, question_field, answer_field)
                    q_field = field_names[qi]
                    a_field = field_names[ai]

                qi, ai = detect_field_mapping(field_names, question_field, answer_field)

                question = strip_html(fields[qi]) if qi < len(fields) else ""
                answer_text = strip_html(fields[ai]) if ai < len(fields) else ""

                # Build extra_fields from remaining fields
                extra: dict[str, str] = {}
                for i, fname in enumerate(field_names):
                    if i != qi and i != ai and i < len(fields):
                        stripped = strip_html(fields[i])
                        if stripped:
                            extra[fname] = stripped

                # Find deck for this note
                did_str = card_deck_map.get(note["id"], "1")
                deck_name = deck_names.get(did_str, "Default")

                if deck_name not in seen_decks:
                    seen_decks.add(deck_name)
                    decks.append(
                        DeckRecord(
                            name=deck_name,
                            source_id=int(did_str) if did_str.isdigit() else None,
                        )
                    )

                note_deck_map[note["id"]] = deck_name
                tags = note["tags"].strip() if note["tags"] else ""

                cards.append(
                    CardRecord(
                        deck_id=0,  # will be set during import
                        question=question,
                        answer=answer_text,
                        extra_fields=extra,
                        tags=tags,
                        source="apkg",
                        source_note_id=note["id"],
                        source_note_guid=note["guid"],
                    )
                )

            field_info: dict[str, list[str] | str] = {
                "fields": all_field_names,
                "question_field": q_field,
                "answer_field": a_field,
            }
            return decks, cards, field_info, note_deck_map

        finally:
            conn.close()


def detect_field_mapping(
    field_names: list[str],
    question_field: str | None,
    answer_field: str | None,
) -> tuple[int, int]:
    """Returns (question_index, answer_index).

    Priority: explicit params > name matching > positional (0, 1).
    """
    names_lower = [n.lower() for n in field_names]

    # Question field
    if question_field:
        try:
            qi = names_lower.index(question_field.lower())
        except ValueError:
            msg = f"Question field '{question_field}' not found. Available: {field_names}"
            raise ValueError(msg) from None
    else:
        qi = find_field_index(names_lower, QUESTION_FIELD_NAMES)
        if qi is None:
            qi = 0

    # Answer field
    if answer_field:
        try:
            ai = names_lower.index(answer_field.lower())
        except ValueError:
            msg = f"Answer field '{answer_field}' not found. Available: {field_names}"
            raise ValueError(msg) from None
    else:
        ai = find_field_index(names_lower, ANSWER_FIELD_NAMES)
        if ai is None:
            ai = 1 if len(field_names) > 1 else 0

    return qi, ai


def find_field_index(names_lower: list[str], candidates: set[str]) -> int | None:
    """Find the first field name that matches a candidate set."""
    for i, name in enumerate(names_lower):
        if name in candidates:
            return i
    return None


def strip_html(html: str) -> str:
    """Strip HTML tags, returning plain text."""
    if not html or "<" not in html:
        return html.strip()
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)
