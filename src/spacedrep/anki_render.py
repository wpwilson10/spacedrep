"""Render Anki card content from raw note fields and model definitions.

Extracted from apkg_reader.py. All rendering is pure: no I/O, no DB access.
"""

import re

from bs4 import BeautifulSoup

QUESTION_FIELD_NAMES = {"front", "question", "prompt", "q"}
ANSWER_FIELD_NAMES = {"back", "answer", "implementation", "a", "response"}

_CLOZE_FIELD_RE = re.compile(r"\{\{cloze:([^}]+)\}\}")

# Matches {{FieldName}}, {{type:FieldName}}, {{hint:FieldName}}, {{#FieldName}}, {{/FieldName}}
_TEMPLATE_FIELD_RE = re.compile(r"\{\{(?:#|/|type:|hint:)?([^}:]+)\}\}")

_ANKI_SPECIAL_FIELDS = frozenset(
    {"FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag"}
)


class ModelInfo:
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


class NoteInfo:
    """Parsed Anki note row."""

    __slots__ = ("mid", "fields", "guid", "tags")

    def __init__(self, mid: str, fields: list[str], guid: str, tags: str) -> None:
        self.mid = mid
        self.fields = fields
        self.guid = guid
        self.tags = tags


def render_card(
    note_flds: str,
    model_info: ModelInfo,
    card_ord: int,
) -> tuple[str, str, dict[str, str]]:
    """Render a card's question, answer, and extra fields from raw note data.

    Args:
        note_flds: Raw field string from notes.flds (\\x1f-separated).
        model_info: Parsed model definition with field names, templates, type.
        card_ord: 0-based card ordinal.

    Returns:
        (question, answer, extra_fields) with HTML stripped.
    """
    fields = note_flds.split("\x1f")
    field_names = model_info.field_names

    if model_info.model_type == 1:
        # Cloze model
        cloze_field_idx = _find_cloze_field(model_info, fields)
        raw_text = fields[cloze_field_idx] if cloze_field_idx < len(fields) else ""
        question_raw, answer_raw = render_cloze(raw_text, card_ord)
        question = strip_html(question_raw)
        answer = strip_html(answer_raw)
        extra: dict[str, str] = {}
        for i, fname in enumerate(field_names):
            if i != cloze_field_idx and i < len(fields):
                stripped = strip_html(fields[i])
                if stripped:
                    extra[fname] = stripped
        return (question, answer, extra)

    if len(model_info.templates) > 1:
        # Multi-template basic model (e.g., basic+reversed)
        return resolve_template_fields(model_info.templates, card_ord, fields, field_names)

    # Single-template basic model — use field name detection
    qi, ai = detect_field_mapping(field_names, None, None)
    return _build_qa_extra(fields, field_names, qi, ai)


def render_cloze(text: str, card_ord: int) -> tuple[str, str]:
    """Render cloze deletion text for a specific card ordinal.

    Args:
        text: Raw cloze text with {{c1::answer}} or {{c1::answer::hint}} syntax.
        card_ord: 0-based card ordinal (ord=0 -> c1, ord=1 -> c2).

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


def strip_html(html: str) -> str:
    """Strip HTML tags, returning plain text."""
    if not html or "<" not in html:
        return html.strip()
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def _find_cloze_field(minfo: ModelInfo, fields: list[str]) -> int:
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
