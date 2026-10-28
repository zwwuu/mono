"""Build fixture PDFs for the tests (reportlab for forms, pypdf for encryption)."""

from pathlib import Path

from pypdf import PdfReader, PdfWriter
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
