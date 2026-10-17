"""Synthetic PDF authoring for parser tests, using ReportLab (BSD)."""
import io
from types import SimpleNamespace
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


class Page:
    def __init__(self, width=595, height=842):
        self.rect = (0, 0, width, height)
        self.operations = []

    def insert_text(self, point, text, fontsize=12):
        self.operations.append(("text", point, text, fontsize))

    def insert_image(self, rect, stream):
        self.operations.append(("image", rect, stream))


class Document:
    def __init__(self):
        self.pages = []

    def new_page(self, width=595, height=842):
        page = Page(width, height)
        self.pages.append(page)
        return page

    def save(self, path):
        c = canvas.Canvas(path)
        for page in self.pages:
            _, _, width, height = page.rect
            c.setPageSize((width, height))
            for kind, *args in page.operations:
                if kind == "text":
                    (x, y), text, size = args
                    c.setFont("Helvetica", size)
                    c.drawString(x, height - y, text)
                else:
                    (x0, y0, x1, y1), stream = args
                    c.drawImage(ImageReader(io.BytesIO(stream)), x0, height - y1, x1-x0, y1-y0)
            c.showPage()
        c.save()

    def close(self):
        pass


builder = SimpleNamespace(open=Document)
