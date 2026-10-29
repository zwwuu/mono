"""End-to-end checker tests on generated PDFs and broken inputs."""

import pytest

from apps.pdf_form_check.checker import FormChecker
from apps.pdf_form_check.errors import FieldsMissingError, InputError, MalformedPdfError
from apps.pdf_form_check.form_reader import PdfFormReader
from tests.pdf_form_check.pdf_factory import (
    make_checkbox_pdf,
    make_encrypted_pdf,
    make_form_pdf,
    make_plain_pdf,
    make_signed_like_pdf,
)


def test_match_a1_yes(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes", "A2": "no"})
    result = FormChecker().check(str(pdf))
    assert result.matched is True
    assert result.matched_fields == ("A1",)


def test_match_a2_case_and_whitespace(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "no", "A2": "  YES  "})
    result = FormChecker().check(str(pdf))
    assert result.matched is True
    assert result.matched_fields == ("A2",)


def test_match_both_yes(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes", "A2": "yes"})
    assert FormChecker().check(str(pdf)).matched_fields == ("A1", "A2")


def test_no_match(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "no", "A2": "off"})
    result = FormChecker().check(str(pdf))
    assert result.matched is False
    assert result.missing_fields == ()


def test_unfilled_form_is_no_match_not_an_error(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": None, "A2": None})
    result = FormChecker().check(str(pdf))
    assert result.matched is False


def test_checked_checkbox_counts_as_yes(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_checkbox_pdf(pdf, "A1", checked=True)
    # A2 is absent, but the A1 match wins over the missing field.
    assert FormChecker().check(str(pdf)).matched is True


def test_unchecked_checkbox_is_no_match(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_checkbox_pdf(pdf, "A1", checked=False)
    with pytest.raises(FieldsMissingError):
        FormChecker().check(str(pdf))


def test_dotted_field_name_resolves(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"form.A1": "yes", "A2": "no"})
    assert FormChecker().check(str(pdf)).matched is True


def test_lowercase_field_name_resolves(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"a1": "yes", "A2": "no"})
    assert FormChecker().check(str(pdf)).matched is True


def test_match_wins_over_missing_field(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "yes"})
    result = FormChecker().check(str(pdf))
    assert result.matched is True
    assert result.missing_fields == ("A2",)


def test_missing_field_without_match_raises(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_form_pdf(pdf, {"A1": "no"})
    with pytest.raises(FieldsMissingError) as exc_info:
        FormChecker().check(str(pdf))
    assert exc_info.value.missing == ("A2",)


def test_plain_pdf_without_fields_raises(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_plain_pdf(pdf)
    with pytest.raises(FieldsMissingError) as exc_info:
        FormChecker().check(str(pdf))
    assert exc_info.value.missing == ("A1", "A2")


def test_missing_file_raises_input_error(tmp_path):
    with pytest.raises(InputError):
        FormChecker().check(str(tmp_path / "nope.pdf"))


def test_directory_raises_input_error(tmp_path):
    with pytest.raises(InputError):
        FormChecker().check(str(tmp_path))


def test_empty_file_is_malformed(tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"")
    with pytest.raises(MalformedPdfError):
        FormChecker().check(str(pdf))


def test_garbage_file_is_malformed(tmp_path):
    pdf = tmp_path / "garbage.pdf"
    pdf.write_bytes(b"this is not a pdf at all\n" * 10)
    with pytest.raises(MalformedPdfError):
        FormChecker().check(str(pdf))


def test_truncated_pdf_is_malformed(tmp_path):
    good = tmp_path / "good.pdf"
    make_form_pdf(good, {"A1": "yes", "A2": "no"})
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(good.read_bytes()[:200])
    with pytest.raises(MalformedPdfError):
        FormChecker().check(str(bad))


def test_encrypted_pdf_is_malformed(tmp_path):
    pdf = tmp_path / "locked.pdf"
    make_encrypted_pdf(pdf, password="secret")
    with pytest.raises(MalformedPdfError):
        FormChecker().check(str(pdf))


def test_signed_pdf_parses_normally(tmp_path):
    # A form with a signature field (constructed, not really signed —
    # see make_signed_like_pdf): the signature field must not
    # interfere, and validity is not checked.
    pdf = tmp_path / "signed.pdf"
    make_signed_like_pdf(pdf, {"A1": "yes", "A2": "no"})
    result = FormChecker().check(str(pdf))
    assert result.matched is True
    assert result.matched_fields == ("A1",)
    fields = PdfFormReader(str(pdf)).read_fields()
    assert isinstance(fields["Signature1"], dict)
