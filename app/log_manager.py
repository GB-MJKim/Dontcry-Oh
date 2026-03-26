import json
from datetime import datetime
from typing import Dict, List, Optional

from .models import InspectionRow
from .settings import INSPECTION_LOG_PATH

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


SEOUL_TZ = ZoneInfo("Asia/Seoul") if ZoneInfo else None


def _now() -> datetime:
    if SEOUL_TZ is not None:
        return datetime.now(SEOUL_TZ)
    return datetime.now()


def _safe_text(value) -> str:
    return "" if value is None else str(value)


def append_error_logs(pdf_filename: str, region: str, rows: List[InspectionRow]) -> int:
    error_rows = [row for row in rows if row.status == "오류"]
    if not error_rows:
        return 0

    timestamp = _now()
    entries: List[Dict] = []
    for row in error_rows:
        entries.append(
            {
                "inspected_at": timestamp.isoformat(),
                "inspected_date": timestamp.strftime("%Y-%m-%d"),
                "inspected_time": timestamp.strftime("%H:%M:%S"),
                "pdf_filename": _safe_text(pdf_filename),
                "region": _safe_text(region),
                "status": _safe_text(row.status),
                "product_name_pdf": _safe_text(row.product_name_pdf),
                "product_name_master": _safe_text(row.product_name_master),
                "match_score": float(row.match_score or 0.0),
                "pdf_spec": _safe_text(row.pdf_spec),
                "master_spec": _safe_text(row.master_spec),
                "pdf_prices": {
                    "spec_price": row.pdf_prices.get("spec_price"),
                    "kg_price": row.pdf_prices.get("kg_price"),
                    "unit_price": row.pdf_prices.get("unit_price"),
                },
                "master_prices": {
                    "spec_price": row.master_prices.get("spec_price"),
                    "kg_price": row.master_prices.get("kg_price"),
                    "unit_price": row.master_prices.get("unit_price"),
                },
                "notes": list(row.notes or []),
            }
        )

    with open(INSPECTION_LOG_PATH, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(entries)


def load_error_logs() -> List[Dict]:
    if not INSPECTION_LOG_PATH.exists():
        return []

    rows: List[Dict] = []
    with open(INSPECTION_LOG_PATH, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    rows.sort(key=lambda item: item.get("inspected_at", ""), reverse=True)
    return rows


def filter_logs(rows: List[Dict], query: str = "") -> List[Dict]:
    query = (query or "").strip().lower()
    if not query:
        return rows

    filtered: List[Dict] = []
    for row in rows:
        haystack = " ".join(
            [
                _safe_text(row.get("pdf_filename")),
                _safe_text(row.get("product_name_pdf")),
                _safe_text(row.get("product_name_master")),
                _safe_text(row.get("region")),
            ]
        ).lower()
        if query in haystack:
            filtered.append(row)
    return filtered
