"""Download the public-domain U.S. government corpus from official .gov sources.

Usage:  uv run python scripts/download_corpus.py

Each document lists official URLs in priority order; the first that returns a
real PDF wins. If every URL fails for a document it is skipped (and the
ingestion step simply ingests whatever is present).

The FAA handbook is ~92 MB / ~600 pages, so we keep only the chapters most
relevant to a floor maintainer and write a trimmed PDF plus a sidecar JSON that
maps each trimmed page back to the handbook's printed page label (e.g. "1-12").
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = ROOT / ".cache" / "downloads"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

DOCS = [
    {
        "file": "osha_3120_loto.pdf",
        "title": "OSHA 3120: Control of Hazardous Energy (Lockout/Tagout)",
        "urls": [
            "https://www.osha.gov/sites/default/files/publications/osha3120.pdf",
            "https://www.osha.gov/Publications/osha3120.pdf",
        ],
    },
    {
        "file": "doe_femp_datacenter_best_practices.pdf",
        "title": "DOE/FEMP Best Practices Guide for Energy-Efficient Data Center Design (2024)",
        "urls": [
            "https://www.energy.gov/sites/default/files/2024-07/best-practice-guide-data-center-design.pdf",
            "https://www.nrel.gov/docs/fy24osti/89843.pdf",
        ],
    },
    {
        "file": "faa_h_8083_30b_full.pdf",
        "title": "FAA-H-8083-30B Aviation Maintenance Technician Handbook - General",
        "urls": [
            "https://www.faa.gov/regulations_policies/handbooks_manuals/aviation/amtg_handbook.pdf",
        ],
        "cache_only": True,  # full PDF lives in .cache; trimmed copy goes to data/raw
    },
]

# Chapters of FAA-H-8083-30B kept for the corpus (by printed chapter number).
FAA_CHAPTERS = {
    1: "Safety, Ground Operations & Servicing",
    10: "Inspection Concepts & Techniques",
    11: "Hand Tools & Measuring Devices",
    14: "Human Factors",
}


def fetch(url: str, dest: Path) -> bool:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"}
    )
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        with open(tmp, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise ValueError("response is not a PDF")
        tmp.replace(dest)
        return True
    except Exception as e:
        print(f"    failed: {url} ({e})")
        tmp.unlink(missing_ok=True)
        return False


def download(doc: dict) -> Path | None:
    target_dir = CACHE if doc.get("cache_only") else RAW
    dest = target_dir / doc["file"]
    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"  [cached] {doc['file']}")
        return dest
    target_dir.mkdir(parents=True, exist_ok=True)
    for url in doc["urls"]:
        print(f"  GET {url}")
        for attempt in range(3):
            if fetch(url, dest):
                print(f"  -> {dest.relative_to(ROOT)} ({dest.stat().st_size / 1e6:.1f} MB)")
                return dest
            time.sleep(2 * (attempt + 1))
    print(f"  SKIPPED {doc['file']}: all official sources failed")
    return None


def chapter_of(label: str) -> int | None:
    head = label.split("-", 1)[0]
    return int(head) if "-" in label and head.isdigit() else None


def trim_faa(full: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    out_pdf = RAW / "faa_h_8083_30b_selected.pdf"
    out_map = RAW / "faa_h_8083_30b_selected.pages.json"
    if out_pdf.exists() and out_map.exists():
        print("  [cached] faa_h_8083_30b_selected.pdf")
        return
    reader = PdfReader(str(full))
    labels = list(reader.page_labels)
    # Printed page labels look like "1-12"; fall back to text sniffing if absent.
    if not any(chapter_of(lbl) for lbl in labels):
        labels = [_sniff_label(p) or str(i + 1) for i, p in enumerate(reader.pages)]
    writer = PdfWriter()
    page_map = []
    for i, page in enumerate(reader.pages):
        ch = chapter_of(labels[i])
        if ch in FAA_CHAPTERS:
            writer.add_page(page)
            page_map.append({"label": labels[i], "orig_index": i + 1, "chapter": ch})
    if not page_map:
        print("  WARNING: could not identify FAA chapters; keeping full handbook")
        out_pdf.write_bytes(full.read_bytes())
        page_map = [{"label": lb, "orig_index": i + 1, "chapter": None} for i, lb in enumerate(labels)]
    else:
        with open(out_pdf, "wb") as f:
            writer.write(f)
    out_map.write_text(json.dumps({"chapters": FAA_CHAPTERS, "pages": page_map}, indent=1))
    print(f"  -> trimmed FAA handbook to {len(page_map)} pages, chapters {sorted(FAA_CHAPTERS)}")


def _sniff_label(page) -> str | None:
    import re

    text = (page.extract_text() or "")[-200:]
    m = re.search(r"\b(\d{1,2}-\d{1,3})\s*$", text.strip())
    return m.group(1) if m else None


def main() -> int:
    print("Downloading corpus (official .gov sources only)")
    got = 0
    for doc in DOCS:
        print(f"* {doc['title']}")
        path = download(doc)
        if path and doc["file"].startswith("faa_"):
            trim_faa(path)
        got += path is not None
    print(f"Done: {got}/{len(DOCS)} documents available in {RAW.relative_to(ROOT)}")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
