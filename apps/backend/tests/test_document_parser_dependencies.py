from __future__ import annotations

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_requirements_declares_document_parser_dependencies():
    requirements = (BACKEND_DIR / "requirements.txt").read_text(encoding="utf-8").lower()

    for package in ("python-docx", "pdfplumber", "pypdfium2", "pytesseract"):
        assert package in requirements


def test_dockerfile_installs_tesseract_runtime():
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")

    assert "tesseract-ocr" in dockerfile
    assert "tesseract-ocr-eng" in dockerfile
    assert "tesseract-ocr-chi-sim" in dockerfile
