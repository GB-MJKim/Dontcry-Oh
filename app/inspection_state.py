import json
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .settings import LAST_INSPECTION_PDF_DIR, LAST_INSPECTION_STATE_PATH


def _serialize_result_row(row: Any) -> dict[str, Any]:
    if is_dataclass(row):
        return asdict(row)
    if isinstance(row, dict):
        return dict(row)
    return dict(getattr(row, "__dict__", {}))


def _serialize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": batch.get("section_id"),
        "display_name": batch.get("display_name"),
        "preview_filename": batch.get("preview_filename"),
        "results": [_serialize_result_row(row) for row in batch.get("results", [])],
        "parsed_count": int(batch.get("parsed_count") or 0),
        "result_count": int(batch.get("result_count") or 0),
        "summary_message": batch.get("summary_message"),
        "error_message": batch.get("error_message"),
        "logged_errors": int(batch.get("logged_errors") or 0),
    }


def save_last_inspection_state(
    *,
    selected_region: str,
    inspection_batches: list[dict[str, Any]],
    parsed_count: int,
    uploaded_pdf_name: str | None,
    uploaded_pdf_names: list[str],
    uploaded_preview_filename: str | None,
    success_message: str | None,
) -> None:
    payload = {
        "selected_region": selected_region,
        "inspection_batches": [_serialize_batch(batch) for batch in inspection_batches],
        "parsed_count": int(parsed_count or 0),
        "uploaded_pdf_name": uploaded_pdf_name,
        "uploaded_pdf_names": list(uploaded_pdf_names or []),
        "uploaded_preview_filename": uploaded_preview_filename,
        "success_message": success_message,
    }
    LAST_INSPECTION_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_last_inspection_state() -> dict[str, Any]:
    if not LAST_INSPECTION_STATE_PATH.exists():
        return {}

    try:
        data = json.loads(LAST_INSPECTION_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    batches = data.get("inspection_batches")
    if not isinstance(batches, list):
        data["inspection_batches"] = []
    return data


def replace_last_inspection_pdfs(files: list[tuple[str, Path]]) -> None:
    LAST_INSPECTION_PDF_DIR.mkdir(exist_ok=True, parents=True)
    for entry in LAST_INSPECTION_PDF_DIR.iterdir():
        try:
            if entry.is_file() or entry.is_symlink():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
        except OSError:
            continue

    for filename, src_path in files:
        if not filename:
            continue
        if not src_path.exists() or not src_path.is_file():
            continue
        shutil.copyfile(src_path, LAST_INSPECTION_PDF_DIR / filename)
