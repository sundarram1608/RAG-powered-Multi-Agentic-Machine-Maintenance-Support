"""
download_documents.py
---------------------
Fetches the project's RAG source PDFs into synthetic_data/documents/:
  - 4 LulzBot FDM user manuals  -> user_manuals/
  - 1 NIOSH safety guide        -> safety_guidelines/

These PDFs are git-ignored (large binaries), so anyone cloning the repo runs
this once to populate them. Sources + licenses are documented in ATTRIBUTIONS.md
(same folder).

Run:  python synthetic_data/documents/download_documents.py

Uses only the Python standard library (urllib), so no extra dependency. Existing
files are skipped, so re-running is safe/idempotent.
"""

import sys
import urllib.request
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent
USER_MANUALS = DOCS_DIR / "user_manuals"
SAFETY = DOCS_DIR / "safety_guidelines"

# (url, destination path) — destinations match the paths in machine_versions.manual_path.
DOWNLOADS = [
    ("https://download.lulzbot.com/Mini/1.04/documentation/Manual/interior_9780989378468.pdf",
     USER_MANUALS / "lulzbot_mini_user_manual.pdf"),
    ("https://download.lulzbot.com/TAZ/6.0/documentation/manual/TAZ-6-Manual.pdf",
     USER_MANUALS / "lulzbot_taz6_user_manual.pdf"),
    ("https://download.lulzbot.com/TAZ/TAZ_WE/v1.0.2/documentation/Manual/TAZ_Workhorse_Manual.pdf",
     USER_MANUALS / "lulzbot_taz_workhorse_user_manual.pdf"),
    ("https://download.lulzbot.com/TAZ/TAZ_Pro/v1.0.3/documentation/manual/TAZPro_Manual.pdf",
     USER_MANUALS / "lulzbot_taz_pro_user_manual.pdf"),
    ("https://www.cdc.gov/niosh/docs/2024-103/pdfs/2024-103.pdf",
     SAFETY / "niosh_safe_3d_printing_2024-103.pdf"),
]

USER_AGENT = "Mozilla/5.0 (compatible; agenticragmcp-downloader/1.0)"


def _download(url: str, dest: Path) -> bool:
    """Download one PDF; skip if already present. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  • skip (exists): {dest.name}")
        return True

    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        dest.write_bytes(data)
    except Exception as exc:  # noqa: BLE001 - report any fetch error and continue
        print(f"  ✗ failed: {dest.name} — {exc}")
        return False

    # Sanity-check that we actually got a PDF.
    if data[:5] != b"%PDF-":
        print(f"  ✗ not a PDF: {dest.name} (got {data[:5]!r})")
        dest.unlink(missing_ok=True)
        return False

    print(f"  ✓ {dest.name}  ({len(data) / (1024 * 1024):.1f} MB)")
    return True


def main() -> int:
    print("Downloading RAG source documents…")
    succeeded = sum(_download(url, dest) for url, dest in DOWNLOADS)
    total = len(DOWNLOADS)
    print(f"\n{succeeded}/{total} documents ready in {DOCS_DIR}")
    return 0 if succeeded == total else 1


if __name__ == "__main__":
    sys.exit(main())
