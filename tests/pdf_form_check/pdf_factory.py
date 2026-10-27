"""Build fixture PDFs for the tests (reportlab for forms, pypdf for
encryption and signature-like fields)."""

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def make_form_pdf(path, fields):
    """A one-page PDF with text fields.

    fields maps a field name to its value; None means the field is
    left unfilled (present but empty).
    """
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 750, "form")
    y = 700
    for name, value in fields.items():
        kwargs = {"name": name, "x": 100, "y": y, "width": 200, "height": 20}
        if value is not None:
            kwargs["value"] = value
        c.acroForm.textfield(**kwargs)
        y -= 30
    c.save()


def make_checkbox_pdf(path, name, checked):
    """A one-page PDF with a single checkbox field."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 750, "form")
    c.acroForm.checkbox(name=name, x=100, y=700, size=20, checked=checked)
    c.save()


def make_plain_pdf(path):
    """A valid PDF with no form fields at all."""
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 750, "no form here")
    c.save()


def make_encrypted_pdf(path, password="secret"):
    """A valid PDF encrypted with `password` (no form fields)."""
    plain = Path(str(path) + ".plain.pdf")
    make_plain_pdf(plain)
    reader = PdfReader(str(plain))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    with open(path, "wb") as f:
        writer.write(f)
    plain.unlink()


def make_signed_like_pdf(path, fields):
    """A form PDF with an added signature field.

    This is NOT a cryptographically signed PDF: the signature value
    carries placeholder ByteRange/Contents, and no key is involved.
    That is enough for the parser test — pypdf does not validate
    signatures, so a constructed /Sig field exercises exactly the
    same reading path as a real one, with no binary fixture and no
    signing library. pypdf has no public API for adding a field, so
    this reaches into the writer's root and object table; that is
    test code only, never shipped app code.
    """
    tmp = Path(str(path) + ".form.pdf")
    make_form_pdf(tmp, fields)
    reader = PdfReader(str(tmp))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    sig_value = DictionaryObject()
    sig_value.update(
        {
            NameObject("/Type"): NameObject("/Sig"),
            NameObject("/Filter"): NameObject("/Adobe.PPKLite"),
            NameObject("/SubFilter"): NameObject("/adbe.pkcs7.detached"),
            NameObject("/ByteRange"): ArrayObject(
                [NumberObject(0), NumberObject(100), NumberObject(200), NumberObject(300)]
            ),
            NameObject("/Contents"): ByteStringObject(b"\x00" * 128),
            NameObject("/M"): TextStringObject("D:20260101000000Z"),
        }
    )
    sig = DictionaryObject()
    sig.update(
        {
            NameObject("/FT"): NameObject("/Sig"),
            NameObject("/T"): TextStringObject("Signature1"),
            NameObject("/V"): sig_value,
        }
    )
    # The field must be an indirect object: pypdf's field wrapper
    # reads its indirect reference, and a direct dict has none.
    acro = writer._root_object[NameObject("/AcroForm")]
    acro[NameObject("/Fields")].append(writer._add_object(sig))
    with open(path, "wb") as f:
        writer.write(f)
    tmp.unlink()
