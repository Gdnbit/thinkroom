"""Small PDFium reader boundary; no dependency on MuPDF or its bindings.

Page methods expose only what ingestion needs. PDFium access stays on the calling
thread; the OCR pool receives detached PIL images, never native PDF handles.
"""
from types import SimpleNamespace

import pypdfium2 as pdfium
import pypdfium2.raw as raw


class Page:
    def __init__(self, document, number):
        self.number = number
        self._document = document
        width, height = document.get_page_size(number)
        self.rect = SimpleNamespace(width=width, height=height)

    def get_text(self, _format="text"):
        page = self._document[self.number]
        try:
            textpage = page.get_textpage()
            try:
                return textpage.get_text_bounded().replace("\r\n", "\n")
            finally:
                textpage.close()
        finally:
            page.close()

    def get_image_info(self, xrefs=True):
        page = self._document[self.number]
        try:
            return [{"bbox": obj.get_bounds()} for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])]
        finally:
            page.close()

    def get_drawings(self):
        page = self._document[self.number]
        try:
            return [None for _ in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH])]
        finally:
            page.close()

    def render_image(self, dpi):
        page = self._document[self.number]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                return bitmap.to_pil().convert("RGB").copy()
            finally:
                bitmap.close()
        finally:
            page.close()


class Document:
    def __init__(self, path):
        self._document = pdfium.PdfDocument(path)

    def __len__(self):
        return len(self._document)

    def __getitem__(self, number):
        if not 0 <= number < len(self):
            raise IndexError(number)
        return Page(self._document, number)

    def __iter__(self):
        return (self[number] for number in range(len(self)))

    def close(self):
        self._document.close()


def open_document(path):
    return Document(path)
