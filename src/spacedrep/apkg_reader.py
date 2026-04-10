"""Parse .apkg files (ZIP'd SQLite) into card records."""

import json
import re
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
) -> tuple[
    list[DeckRecord],
    list[CardRecord],
    dict[str, list[str] | str],
    dict[tuple[int, int], str],
]:
    """Read an .apkg file and return (decks, cards, field_info, note_deck_map).

    field_info contains: fields (list of field names), question_field, answer_field.
    note_deck_map maps (source_note_id, source_card_ord) to deck name.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        with zipfile.ZipFile(apkg_path, "r") as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    msg = f"Refusing to extract potentially unsafe path: {member}"
                    raise ValueError(msg)
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

            # Build model lookup: {model_id: {field_names, templates, type}}
            model_info: dict[str, _ModelInfo] = {}
            for mid, model in models_json.items():
                model_info[mid] = _ModelInfo(
                    field_names=[f["name"] for f in model["flds"]],
                    templates=model.get("tmpls", []),
                    model_type=model.get("type", 0),
                )

            # Build deck name mapping: {deck_id: deck_name}
            deck_names: dict[str, str] = {}
            for did, deck_data in decks_json.items():
                deck_names[did] = deck_data["name"]

            # Build note lookup: {note_id: {mid, fields, guid, tags}}
            note_lookup: dict[int, _NoteInfo] = {}
            for row in conn.execute("SELECT id, mid, flds, guid, tags FROM notes").fetchall():
                note_lookup[row["id"]] = _NoteInfo(
                    mid=str(row["mid"]),
                    fields=row["flds"].split("\x1f"),
                    guid=row["guid"],
                    tags=" ".join(row["tags"].split()) if row["tags"] else "",
                )

            # Iterate cards table — each Anki card becomes one CardRecord
            all_field_names: list[str] = []
            q_field = ""
            a_field = ""
            decks: list[DeckRecord] = []
            cards: list[CardRecord] = []
            note_deck_map: dict[tuple[int, int], str] = {}
            seen_decks: set[str] = set()

            for card_row in conn.execute("SELECT nid, did, ord, queue FROM cards").fetchall():
                nid: int = card_row["nid"]
                did_str = str(card_row["did"])
                card_ord: int = card_row["ord"]
                queue: int = card_row["queue"]

                note = note_lookup.get(nid)
                if note is None:
                    continue  # orphan card row

                minfo = model_info.get(note.mid)
                if minfo is None:
                    continue  # unknown model

                field_names = minfo.field_names
                fields = note.fields

                # Capture field_info from the first model seen (best-effort)
                if not all_field_names and field_names:
                    all_field_names = field_names
                    qi, ai = detect_field_mapping(field_names, question_field, answer_field)
                    q_field = field_names[qi]
                    a_field = field_names[ai]

                # Dispatch based on model type and template count
                if minfo.model_type == 1:
                    # Cloze model: render cloze for this card's ordinal
                    cloze_field_idx = _find_cloze_field(minfo, fields)
                    raw_text = fields[cloze_field_idx] if cloze_field_idx < len(fields) else ""
                    question_raw, answer_raw = render_cloze(raw_text, card_ord)
                    question_text = strip_html(question_raw)
                    answer_text = strip_html(answer_raw)
                    # Extra fields from non-cloze fields
                    extra: dict[str, str] = {}
                    for i, fname in enumerate(field_names):
                        if i != cloze_field_idx and i < len(fields):
                            stripped = strip_html(fields[i])
                            if stripped:
                                extra[fname] = stripped
                    extra["_cloze_source"] = raw_text
                elif len(minfo.templates) > 1:
                    # Multi-template basic model (e.g., basic+reversed)
                    question_text, answer_text, extra = resolve_template_fields(
                        minfo.templates, card_ord, fields, field_names
                    )
                else:
                    # Single-template basic model — use field name detection
                    qi, ai = detect_field_mapping(field_names, question_field, answer_field)
                    question_text, answer_text, extra = _build_qa_extra(fields, field_names, qi, ai)

                # Resolve deck
                deck_name = deck_names.get(did_str, "Default")
                if deck_name not in seen_decks:
                    seen_decks.add(deck_name)
                    decks.append(
                        DeckRecord(
                            name=deck_name,
                            source_id=int(did_str) if did_str.isdigit() else None,
                        )
                    )

                note_deck_map[(nid, card_ord)] = deck_name

                cards.append(
                    CardRecord(
                        deck_id=0,  # will be set during import
                        question=question_text,
                        answer=answer_text,
                        extra_fields=extra,
                        tags=note.tags,
                        source="apkg",
                        source_note_id=nid,
                        source_note_guid=note.guid,
                        source_card_ord=card_ord,
                        suspended=queue == -1,
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


class _ModelInfo:
    """Parsed Anki model (note type) metadata."""

    __slots__ = ("field_names", "templates", "model_type")

    def __init__(
        self,
        field_names: list[str],
        templates: list[dict[str, str]],
        model_type: int,
    ) -> None:
        self.field_names = field_names
        self.templates = templates
        self.model_type = model_type


class _NoteInfo:
    """Parsed Anki note row."""

    __slots__ = ("mid", "fields", "guid", "tags")

    def __init__(self, mid: str, fields: list[str], guid: str, tags: str) -> None:
        self.mid = mid
        self.fields = fields
        self.guid = guid
        self.tags = tags


_CLOZE_FIELD_RE = re.compile(r"\{\{cloze:([^}]+)\}\}")


def _find_cloze_field(minfo: _ModelInfo, fields: list[str]) -> int:
    """Find the field index containing cloze content.

    Checks templates for {{cloze:FieldName}}, falls back to first field with
    cloze syntax.
    """
    # Check templates for {{cloze:FieldName}}
    field_map = {name: i for i, name in enumerate(minfo.field_names)}
    for tmpl in minfo.templates:
        m = _CLOZE_FIELD_RE.search(tmpl.get("qfmt", ""))
        if m:
            name = m.group(1).strip()
            if name in field_map:
                return field_map[name]

    # Fallback: first field that contains cloze markers
    cloze_marker = re.compile(r"\{\{c\d+::")
    for i, fval in enumerate(fields):
        if cloze_marker.search(fval):
            return i

    return 0  # last resort


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


def render_cloze(text: str, card_ord: int) -> tuple[str, str]:
    """Render cloze deletion text for a specific card ordinal.

    Args:
        text: Raw cloze text with {{c1::answer}} or {{c1::answer::hint}} syntax.
        card_ord: 0-based card ordinal (ord=0 → c1, ord=1 → c2).

    Returns:
        (question, answer) tuple with cloze markers resolved to plain text.
    """
    active_num = card_ord + 1
    pattern = r"\{\{c(\d+)::(.+?)(?:::(.+?))?\}\}"

    def q_replace(m: re.Match[str]) -> str:
        if int(m.group(1)) == active_num:
            return f"[{m.group(3)}]" if m.group(3) else "[...]"
        return m.group(2)

    def a_replace(m: re.Match[str]) -> str:
        return m.group(2)

    question = re.sub(pattern, q_replace, text, flags=re.DOTALL)
    answer = re.sub(pattern, a_replace, text, flags=re.DOTALL)
    return (question, answer)


_ANKI_SPECIAL_FIELDS = frozenset(
    {"FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag"}
)

# Matches {{FieldName}}, {{type:FieldName}}, {{hint:FieldName}}, {{#FieldName}}, {{/FieldName}}
_TEMPLATE_FIELD_RE = re.compile(r"\{\{(?:#|/|type:|hint:)?([^}:]+)\}\}")


def resolve_template_fields(
    templates: list[dict[str, str]],
    card_ord: int,
    fields: list[str],
    field_names: list[str],
    question_field: str | None = None,
    answer_field: str | None = None,
) -> tuple[str, str, dict[str, str]]:
    """Resolve Q/A from a model template at a given ordinal.

    Args:
        templates: Model template list, each with 'qfmt' and 'afmt' keys.
        card_ord: 0-based template index.
        fields: Field values (split from note's flds).
        field_names: Field names from the model.
        question_field: Explicit question field override (ignored, uses template).
        answer_field: Explicit answer field override (ignored, uses template).

    Returns:
        (question, answer, extra_fields) tuple with HTML stripped.
    """
    if card_ord >= len(templates):
        # Fallback: shouldn't happen, but be safe
        qi, ai = detect_field_mapping(field_names, question_field, answer_field)
        return _build_qa_extra(fields, field_names, qi, ai)

    tmpl = templates[card_ord]
    field_map = {name: i for i, name in enumerate(field_names)}

    q_idx = _first_real_field(tmpl.get("qfmt", ""), field_map)
    a_idx = _first_real_field(tmpl.get("afmt", ""), field_map)

    if q_idx is None or a_idx is None:
        qi, ai = detect_field_mapping(field_names, question_field, answer_field)
        return _build_qa_extra(fields, field_names, qi, ai)

    return _build_qa_extra(fields, field_names, q_idx, a_idx)


def _first_real_field(template_text: str, field_map: dict[str, int]) -> int | None:
    """Find the first non-special field index referenced in a template string."""
    for m in _TEMPLATE_FIELD_RE.finditer(template_text):
        name = m.group(1).strip()
        if name not in _ANKI_SPECIAL_FIELDS and name in field_map:
            return field_map[name]
    return None


def _build_qa_extra(
    fields: list[str], field_names: list[str], qi: int, ai: int
) -> tuple[str, str, dict[str, str]]:
    """Build (question, answer, extra_fields) from field indices."""
    question = strip_html(fields[qi]) if qi < len(fields) else ""
    answer = strip_html(fields[ai]) if ai < len(fields) else ""
    extra: dict[str, str] = {}
    for i, fname in enumerate(field_names):
        if i != qi and i != ai and i < len(fields):
            stripped = strip_html(fields[i])
            if stripped:
                extra[fname] = stripped
    return (question, answer, extra)


def strip_html(html: str) -> str:
    """Strip HTML tags, returning plain text."""
    if not html or "<" not in html:
        return html.strip()
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)
