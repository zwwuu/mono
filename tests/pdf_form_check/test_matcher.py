"""Tests for YesMatcher: normalization is forgiving, matching is strict."""

import pytest

from pdf_form_check.matcher import YesMatcher

MATCHER = YesMatcher()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("yes", "yes"),
        ("YES", "yes"),
        ("Yes", "yes"),
        ("  yes  ", "yes"),
        ("/Yes", "yes"),
        (" /Yes ", "yes"),
        ("/Off", "off"),
        ("no", "no"),
        ("yess", "yess"),
        ("yes/no", "yes/no"),
    ],
)
def test_normalize(raw, expected):
    assert MATCHER.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["yes", "YES", "Yes", "  yes  ", "/Yes"])
def test_is_yes_true(raw):
    assert MATCHER.is_yes(raw) is True


@pytest.mark.parametrize("raw", [None, "", "no", "yess", "on", "1", "/Off"])
def test_is_yes_false(raw):
    assert MATCHER.is_yes(raw) is False
