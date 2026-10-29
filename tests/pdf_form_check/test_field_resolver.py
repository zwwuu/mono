"""Tests for FieldResolver's precedence: exact, dotted, case-insensitive."""

from apps.pdf_form_check.field_resolver import FieldResolver


def test_exact_match_wins():
    resolver = FieldResolver({"A1": "yes", "form.A1": "no", "a1": "no"})
    assert resolver.resolve("A1") == "A1"


def test_last_segment_match():
    resolver = FieldResolver({"form.A1": "yes"})
    assert resolver.resolve("A1") == "form.A1"


def test_last_segment_needs_the_dot():
    resolver = FieldResolver({"XA1": "yes"})
    assert resolver.resolve("A1") is None


def test_case_insensitive_match():
    resolver = FieldResolver({"a1": "yes"})
    assert resolver.resolve("A1") == "a1"


def test_case_insensitive_last_segment_match():
    resolver = FieldResolver({"Form.a1": "yes"})
    assert resolver.resolve("A1") == "Form.a1"


def test_ambiguous_match_is_deterministic():
    resolver = FieldResolver({"S2.A1": "no", "S1.A1": "yes"})
    assert resolver.resolve("A1") == "S1.A1"


def test_missing_returns_none():
    assert FieldResolver({"B1": "yes"}).resolve("A1") is None
    assert FieldResolver({}).resolve("A1") is None
    assert FieldResolver(None).resolve("A1") is None
