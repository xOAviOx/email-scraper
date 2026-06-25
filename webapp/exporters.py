"""Turn a finished job's leads.csv into the user's chosen download format.

CSV is served as-is (the worker already wrote it). XLSX reuses the styling in
format_leads.py. JSON is built fresh with emails as a proper array.
"""

import csv as _csv
import io
import json
from pathlib import Path

import format_leads  # repo-root module — run the app from the repo root

# (bytes, media_type, file_extension) per format
_MEDIA = {
    "csv": ("text/csv", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "json": ("application/json", "json"),
}


def available_formats() -> list[str]:
    return list(_MEDIA)


def _read_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(_csv.DictReader(f))


def export(csv_path: Path, fmt: str) -> tuple[bytes, str, str]:
    """Return (data, media_type, extension) for the requested format."""
    if fmt not in _MEDIA:
        raise ValueError(f"unsupported format: {fmt}")
    media_type, ext = _MEDIA[fmt]

    if fmt == "csv":
        return Path(csv_path).read_bytes(), media_type, ext

    if fmt == "json":
        rows = []
        for r in _read_rows(Path(csv_path)):
            emails = [e for e in (r.get("emails") or "").split(";") if e.strip()]
            rows.append({
                "category": r.get("category", ""),
                "location": r.get("location", ""),
                "name": r.get("name", ""),
                "website": r.get("website", ""),
                "phone": r.get("phone", ""),
                "address": r.get("address", ""),
                "rating": r.get("rating", ""),
                "emails": emails,
            })
        data = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
        return data, media_type, ext

    # xlsx — openpyxl's Workbook.save() accepts a file-like object.
    rows = format_leads.load_rows(str(csv_path))
    buf = io.BytesIO()
    format_leads.build_workbook(rows, buf)
    return buf.getvalue(), media_type, ext
