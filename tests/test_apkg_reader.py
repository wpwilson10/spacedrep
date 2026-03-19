"""Tests for apkg_reader module."""

import pytest

from spacedrep.apkg_reader import detect_field_mapping, strip_html


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
