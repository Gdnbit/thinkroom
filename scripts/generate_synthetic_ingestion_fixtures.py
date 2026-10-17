"""Regenerate only project-owned synthetic ingestion fixtures and their contract."""
import hashlib
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests/fixtures/synthetic-ingestion"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "demo.txt").write_text("Synthetic library trial: visits rose from 80 to 96. Costs remain unknown.\n", encoding="utf-8")
    for filename, count, chinese in [("long.pdf", 102, False), ("chinese.pdf", 12, True), ("table.pdf", 8, False)]:
        c = canvas.Canvas(str(DEST / filename), invariant=1)
        if chinese:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        for number in range(1, count + 1):
            if filename != "long.pdf" or number not in {1, 102}:
                c.setFont("STSong-Light" if chinese else "Helvetica", 12)
                text = f"合成测试：社区图书馆第{number}页，访问增加但成本未知。" if chinese else f"Synthetic library report page {number}: visits increased, costs remain unknown."
                c.drawString(60, 740, text)
            c.showPage()
        c.save()
    Image.new("RGB", (1080, 32721), "white").save(DEST / "long-image.png")
    (DEST / "long-image-copy.png").write_bytes((DEST / "long-image.png").read_bytes())

    def case(case_id, filename, expected, anchors):
        p = DEST / filename
        return {"case_id": case_id, "execution_mode": "offline", "path": p.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size,
                "expected": expected, "anchors": [{"text": text} for text in anchors]}

    cases = [
        case("IQ-TXT-001", "demo.txt", {"method": "read_text", "min_text_chars": 50}, ["visits rose from 80 to 96"]),
        case("IQ-PDF-TEXT-LONG-001", "long.pdf", {"method": "pdfium_text", "pages_total": 102, "nonempty_pages": 100, "empty_pages": [1, 102], "min_text_chars": 6000, "external_calls": 0}, ["Synthetic library report page 2", "Synthetic library report page 101"]),
        case("IQ-PDF-TEXT-ZH-001", "chinese.pdf", {"method": "pdfium_text", "pages_total": 12, "nonempty_pages": 12, "min_text_chars": 200, "external_calls": 0}, ["社区图书馆", "成本未知"]),
        case("IQ-PDF-TEXT-TABLE-001", "table.pdf", {"method": "pdfium_text", "pages_total": 8, "nonempty_pages": 8, "min_text_chars": 400, "external_calls": 0}, ["Synthetic library report page 8"]),
        case("IQ-DUP-001", "long-image.png", {"width": 1080, "height": 32721, "slices": 21, "identical": True, "external_calls": 0}, []),
    ]
    cases[-1]["paths"] = [cases[-1].pop("path"), "tests/fixtures/synthetic-ingestion/long-image-copy.png"]
    suite = {"schema_version": 1, "suite_id": "synthetic-public-ingestion-v1", "policy": {"max_external_calls": 0}, "cases": cases}
    (ROOT / "evals/ingestion-quality/suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DEST / "README.md").write_text("# Synthetic ingestion fixtures\n\nOriginal generated test material, Apache-2.0. Regenerate with `python scripts/generate_synthetic_ingestion_fixtures.py`. No real reports or user data.\n", encoding="utf-8")
    print("Generated five synthetic ingestion cases; no private source was read.")


if __name__ == "__main__":
    main()
