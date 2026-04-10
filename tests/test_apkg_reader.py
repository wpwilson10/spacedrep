"""Tests for apkg_reader module."""

import pytest

from spacedrep.apkg_reader import (
    detect_field_mapping,
    render_cloze,
    resolve_template_fields,
    strip_html,
)


def test_strip_html_basic() -> None:
    assert strip_html("<b>bold</b> text") == "bold text"


def test_strip_html_no_html() -> None:
    assert strip_html("plain text") == "plain text"


def test_strip_html_empty() -> None:
    assert strip_html("") == ""


def test_strip_html_nested() -> None:
    result = strip_html("<div><p>Hello</p><p>World</p></div>")
    assert "Hello" in result
    assert "World" in result


def test_detect_field_mapping_explicit() -> None:
    fields = ["Prompt", "Implementation", "Topic"]
    qi, ai = detect_field_mapping(fields, "Prompt", "Implementation")
    assert qi == 0
    assert ai == 1


def test_detect_field_mapping_auto() -> None:
    fields = ["Front", "Back", "Notes"]
    qi, ai = detect_field_mapping(fields, None, None)
    assert qi == 0  # "front" matches QUESTION_FIELD_NAMES
    assert ai == 1  # "back" matches ANSWER_FIELD_NAMES


def test_detect_field_mapping_positional_fallback() -> None:
    fields = ["Alpha", "Beta", "Gamma"]
    qi, ai = detect_field_mapping(fields, None, None)
    assert qi == 0  # positional fallback
    assert ai == 1


def test_detect_field_mapping_explicit_not_found() -> None:
    fields = ["Front", "Back"]
    with pytest.raises(ValueError, match="not found"):
        detect_field_mapping(fields, "Nonexistent", None)


def test_detect_field_mapping_case_insensitive() -> None:
    fields = ["QUESTION", "ANSWER"]
    qi, ai = detect_field_mapping(fields, None, None)
    assert qi == 0
    assert ai == 1


# --- render_cloze tests ---


def test_render_cloze_basic() -> None:
    text = "{{c1::Ottawa}} is the capital of Canada"
    q, a = render_cloze(text, 0)
    assert q == "[...] is the capital of Canada"
    assert a == "Ottawa is the capital of Canada"


def test_render_cloze_with_hint() -> None:
    text = "{{c1::Ottawa::capital city}} is the capital of Canada"
    q, a = render_cloze(text, 0)
    assert q == "[capital city] is the capital of Canada"
    assert a == "Ottawa is the capital of Canada"


def test_render_cloze_multi_cloze() -> None:
    text = "{{c1::Ottawa}} is the capital of {{c2::Canada}}"
    # Card 0 (c1 active): Ottawa blanked, Canada shown
    q0, a0 = render_cloze(text, 0)
    assert q0 == "[...] is the capital of Canada"
    assert a0 == "Ottawa is the capital of Canada"
    # Card 1 (c2 active): Ottawa shown, Canada blanked
    q1, a1 = render_cloze(text, 1)
    assert q1 == "Ottawa is the capital of [...]"
    assert a1 == "Ottawa is the capital of Canada"


def test_render_cloze_same_number_multi_blank() -> None:
    text = "{{c1::Ottawa}} is the capital of {{c1::Canada}}"
    q, a = render_cloze(text, 0)
    assert q == "[...] is the capital of [...]"
    assert a == "Ottawa is the capital of Canada"


def test_render_cloze_no_cloze_markers() -> None:
    text = "Plain text with no cloze"
    q, a = render_cloze(text, 0)
    assert q == text
    assert a == text


def test_render_cloze_empty_text() -> None:
    q, a = render_cloze("", 0)
    assert q == ""
    assert a == ""


def test_render_cloze_empty_content() -> None:
    """Empty cloze content {{c1::}} doesn't match render regex — passes through unchanged."""
    q, a = render_cloze("{{c1::}}", 0)
    assert q == "{{c1::}}"
    assert a == "{{c1::}}"


# --- resolve_template_fields tests ---

_BASIC_REVERSED_TEMPLATES = [
    {"qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr id=answer>{{Back}}"},
    {"qfmt": "{{Back}}", "afmt": "{{FrontSide}}<hr id=answer>{{Front}}"},
]

_FIELD_NAMES = ["Front", "Back"]
_FIELD_VALUES = ["What is Python?", "A programming language"]


def test_resolve_template_fields_forward() -> None:
    q, a, extra = resolve_template_fields(_BASIC_REVERSED_TEMPLATES, 0, _FIELD_VALUES, _FIELD_NAMES)
    assert q == "What is Python?"
    assert a == "A programming language"
    assert extra == {}


def test_resolve_template_fields_reversed() -> None:
    q, a, extra = resolve_template_fields(_BASIC_REVERSED_TEMPLATES, 1, _FIELD_VALUES, _FIELD_NAMES)
    assert q == "A programming language"
    assert a == "What is Python?"
    assert extra == {}


def test_resolve_template_fields_with_prefix() -> None:
    templates = [
        {"qfmt": "{{type:Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}"},
    ]
    q, a, _extra = resolve_template_fields(templates, 0, _FIELD_VALUES, _FIELD_NAMES)
    assert q == "What is Python?"
    assert a == "A programming language"


def test_resolve_template_fields_extra_fields() -> None:
    names = ["Front", "Back", "Notes"]
    values = ["Q?", "A!", "Some notes"]
    templates = [
        {"qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}"},
    ]
    q, a, extra = resolve_template_fields(templates, 0, values, names)
    assert q == "Q?"
    assert a == "A!"
    assert extra == {"Notes": "Some notes"}


def test_resolve_template_fields_fallback() -> None:
    # Templates that reference no real fields → falls back to detect_field_mapping
    templates = [{"qfmt": "{{FrontSide}}", "afmt": "{{Tags}}"}]
    names = ["Front", "Back"]
    values = ["Q?", "A!"]
    q, a, _extra = resolve_template_fields(templates, 0, values, names)
    assert q == "Q?"  # positional fallback: index 0
    assert a == "A!"  # positional fallback: index 1


def test_resolve_template_fields_ord_out_of_range() -> None:
    # Card ord beyond template count → falls back to detect_field_mapping
    templates = [{"qfmt": "{{Front}}", "afmt": "{{Back}}"}]
    q, a, _extra = resolve_template_fields(templates, 5, _FIELD_VALUES, _FIELD_NAMES)
    assert q == "What is Python?"
    assert a == "A programming language"
