import math
import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import data_manager
from . import inspection_state
from .comparator import compare_items, inspect_items
from . import log_manager
from .pdf_parser import parse_pdf
from .revalidator import revalidate_error_items
from .settings import LAST_INSPECTION_PDF_DIR, OPENAI_MODEL, TEMP_DIR

app = FastAPI(title="Don't Cry, Oh!")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

REGIONS = ["수도권", "경상권", "경남부산", "호남권"]
MASTER_PAGE_SIZE = 25
LOG_PAGE_SIZE = 20


def _clear_temp_dir() -> None:
    temp_root = Path(TEMP_DIR)
    temp_root.mkdir(exist_ok=True, parents=True)
    for entry in temp_root.iterdir():
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
        except OSError:
            continue


@app.on_event("startup")
def clear_temp_dir_on_startup():
    _clear_temp_dir()


def _base_context(request: Request) -> dict:
    master_rows = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(master_rows)
    metadata = data_manager.load_master_metadata()
    log_count = len(log_manager.load_error_logs())
    return {
        "request": request,
        "regions": REGIONS,
        "master_count": summary["count"],
        "log_count": log_count,
        "normalize_spec": data_manager.normalize_spec,
        "columns": summary["columns"],
        "rows": summary["rows"][:20],
        "results": None,
        "error_message": None,
        "success_message": None,
        "parsed_count": 0,
        "selected_region": "수도권",
        "uploaded_pdf_name": None,
        "uploaded_pdf_names": [],
        "uploaded_pdf_url": None,
        "inspection_batches": [],
        "openai_model": OPENAI_MODEL,
        "master_source_filename": metadata.get("source_filename"),
        "master_source_date": metadata.get("source_date"),
        "master_source_date_iso": metadata.get("source_date_iso"),
        "master_source_date_raw": metadata.get("source_date_raw"),
    }


def _master_matches_query(row: dict, query: str) -> bool:
    query = (query or "").strip().lower()
    if not query:
        return True

    search_fields = [
        row.get("code"),
        row.get("name"),
        row.get("spec"),
    ]
    haystack = " ".join(str(value or "") for value in search_fields).lower()
    return query in haystack


def _master_table_context(query: str = "", page: int = 1) -> dict:
    master_rows = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(master_rows)
    columns = summary["columns"]

    filtered_rows = [row for row in master_rows if _master_matches_query(row, query)]
    filtered_count = len(filtered_rows)
    total_pages = max(1, math.ceil(filtered_count / MASTER_PAGE_SIZE)) if filtered_count else 1
    current_page = min(max(1, int(page or 1)), total_pages)

    start = (current_page - 1) * MASTER_PAGE_SIZE
    end = start + MASTER_PAGE_SIZE
    paged_rows = [{column: row.get(column) for column in columns} for row in filtered_rows[start:end]]

    page_start = max(1, current_page - 2)
    page_end = min(total_pages, current_page + 2)
    page_numbers = list(range(page_start, page_end + 1))
    start_display = start + 1 if filtered_count else 0
    end_display = min(end, filtered_count) if filtered_count else 0

    return {
        "columns": columns,
        "rows": paged_rows,
        "master_query": query,
        "master_filtered_count": filtered_count,
        "master_total_pages": total_pages,
        "master_page": current_page,
        "master_page_numbers": page_numbers,
        "master_has_prev": current_page > 1,
        "master_has_next": current_page < total_pages,
        "master_prev_page": current_page - 1,
        "master_next_page": current_page + 1,
        "master_start_display": start_display,
        "master_end_display": end_display,
        "master_page_size": MASTER_PAGE_SIZE,
    }


def _log_table_context(query: str = "", page: int = 1) -> dict:
    all_logs = log_manager.load_error_logs()
    filtered_logs = log_manager.filter_logs(all_logs, query=query)
    filtered_count = len(filtered_logs)
    total_pages = max(1, math.ceil(filtered_count / LOG_PAGE_SIZE)) if filtered_count else 1
    current_page = min(max(1, int(page or 1)), total_pages)

    start = (current_page - 1) * LOG_PAGE_SIZE
    end = start + LOG_PAGE_SIZE
    paged_logs = filtered_logs[start:end]
    page_start = max(1, current_page - 2)
    page_end = min(total_pages, current_page + 2)

    return {
        "log_rows": paged_logs,
        "log_query": query,
        "log_filtered_count": filtered_count,
        "log_total_pages": total_pages,
        "log_page": current_page,
        "log_page_numbers": list(range(page_start, page_end + 1)),
        "log_has_prev": current_page > 1,
        "log_has_next": current_page < total_pages,
        "log_prev_page": current_page - 1,
        "log_next_page": current_page + 1,
        "log_start_display": start + 1 if filtered_count else 0,
        "log_end_display": min(end, filtered_count) if filtered_count else 0,
    }


def _normalize_upload_name(filename: str | None, index: int) -> str:
    candidate = os.path.basename((filename or "").strip())
    if not candidate:
        candidate = f"upload_{index}.pdf"
    return candidate


def _build_temp_pdf_name(display_name: str) -> str:
    base = Path(display_name)
    stem = base.stem.strip() or "upload"
    suffix = base.suffix.lower() if base.suffix else ".pdf"
    safe_stem = "".join(ch for ch in stem if ch not in '<>:"/\\|?*').strip().rstrip(".") or "upload"
    return f"{safe_stem}_{uuid4().hex[:8]}{suffix}"


def _build_preview_url(request: Request, filename: str | None) -> str | None:
    if not filename:
        return None
    return str(request.url_for("preview_pdf", filename=filename))


def _find_preview_pdf_path(filename: str | None) -> Path | None:
    safe_name = os.path.basename((filename or "").strip())
    if not safe_name or safe_name != filename:
        return None

    for root in (Path(TEMP_DIR), Path(LAST_INSPECTION_PDF_DIR)):
        try:
            candidate = (root.resolve() / safe_name).resolve()
            candidate.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate
    return None


def _cached_home_context(request: Request) -> dict:
    cached = inspection_state.load_last_inspection_state()
    if not cached:
        return {}

    inspection_batches = []
    for batch in cached.get("inspection_batches", []):
        preview_filename = batch.get("preview_filename")
        preview_url = _build_preview_url(request, preview_filename) if _find_preview_pdf_path(preview_filename) else None
        inspection_batches.append(
            {
                "section_id": batch.get("section_id"),
                "display_name": batch.get("display_name"),
                "preview_filename": preview_filename,
                "preview_url": preview_url,
                "results": list(batch.get("results") or []),
                "parsed_count": int(batch.get("parsed_count") or 0),
                "result_count": int(batch.get("result_count") or 0),
                "summary_message": batch.get("summary_message"),
                "error_message": batch.get("error_message"),
                "logged_errors": int(batch.get("logged_errors") or 0),
            }
        )

    first_result_batch = next((batch for batch in inspection_batches if batch.get("results")), None)
    uploaded_preview_filename = cached.get("uploaded_preview_filename")
    uploaded_pdf_url = (
        _build_preview_url(request, uploaded_preview_filename)
        if _find_preview_pdf_path(uploaded_preview_filename)
        else None
    )

    return {
        "results": first_result_batch.get("results") if first_result_batch else None,
        "parsed_count": int(cached.get("parsed_count") or 0),
        "selected_region": cached.get("selected_region") or REGIONS[0],
        "uploaded_pdf_name": cached.get("uploaded_pdf_name"),
        "uploaded_pdf_names": list(cached.get("uploaded_pdf_names") or []),
        "uploaded_pdf_url": uploaded_pdf_url,
        "inspection_batches": inspection_batches,
    }


def render_home(request: Request, **kwargs):
    context = _base_context(request)
    context.update(_cached_home_context(request))
    context["active_page"] = "home"
    context.update(kwargs)
    return templates.TemplateResponse("index.html", context)


def render_master(request: Request, query: str = "", page: int = 1, **kwargs):
    context = _base_context(request)
    context.update(_master_table_context(query=query, page=page))
    context["active_page"] = "master"
    context.update(kwargs)
    return templates.TemplateResponse("master.html", context)


def render_logs(request: Request, query: str = "", page: int = 1, **kwargs):
    context = _base_context(request)
    context.update(_log_table_context(query=query, page=page))
    context["active_page"] = "logs"
    context.update(kwargs)
    return templates.TemplateResponse("logs.html", context)


def _build_inspection_message(results, review_summary):
    if not results:
        return "상품을 찾지 못했습니다."

    parts = ["검수가 완료되었습니다."]
    attempted = int(review_summary.get("attempted") or 0)
    updated_items = int(review_summary.get("updated_items") or 0)
    if review_summary.get("error"):
        parts.append("2차 AI 재검증은 수행하지 못해 1차 결과를 표시합니다.")
    elif attempted:
        parts.append(f"오류 {attempted}건을 2차 AI로 재검증했습니다.")
        if updated_items:
            parts.append(f"{updated_items}건에 수정값을 반영했습니다.")
        else:
            parts.append("추가로 반영할 수정값은 없었습니다.")
    return " ".join(parts)


async def _inspect_single_pdf(
    request: Request,
    region: str,
    pdf_file: UploadFile,
    master_rows,
    batch_index: int,
):
    display_name = _normalize_upload_name(pdf_file.filename, batch_index)
    batch = {
        "section_id": f"inspection-batch-{batch_index}",
        "display_name": display_name,
        "preview_filename": None,
        "preview_url": None,
        "preview_path": None,
        "results": [],
        "parsed_count": 0,
        "result_count": 0,
        "summary_message": None,
        "error_message": None,
        "logged_errors": 0,
    }

    if not display_name.lower().endswith(".pdf"):
        batch["error_message"] = "PDF 파일만 업로드할 수 있습니다."
        return batch

    temp_name = _build_temp_pdf_name(display_name)
    pdf_path = Path(TEMP_DIR) / temp_name
    with open(pdf_path, "wb") as handle:
        handle.write(await pdf_file.read())

    batch["preview_filename"] = temp_name
    batch["preview_path"] = str(pdf_path)
    batch["preview_url"] = _build_preview_url(request, temp_name)

    try:
        items = parse_pdf(str(pdf_path), master_rows)
        review_summary = {"attempted": 0, "updated_items": 0, "updated_fields": 0, "error": None}
        if items:
            initial_contexts = inspect_items(items, master_rows, region)
            review_summary = revalidate_error_items(items, initial_contexts, region)

        results = compare_items(items, master_rows, region)
        logged_errors = log_manager.append_error_logs(display_name, region, results)
        summary_message = _build_inspection_message(results, review_summary)
        if logged_errors:
            summary_message = f"{summary_message} 오류 로그 {logged_errors}건을 저장했습니다."

        batch.update(
            {
                "results": results,
                "parsed_count": len(items),
                "result_count": len(results),
                "summary_message": summary_message,
                "logged_errors": logged_errors,
            }
        )
        return batch
    except RuntimeError as exc:
        batch["error_message"] = str(exc)
        return batch
    except Exception as exc:
        batch["error_message"] = f"검수 중 오류가 발생했습니다: {exc}"
        return batch


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render_home(request)


@app.get("/master", response_class=HTMLResponse)
def master_page(request: Request, q: str = "", page: int = 1):
    return render_master(request, query=q, page=page)


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, q: str = "", page: int = 1):
    return render_logs(request, query=q, page=page)


@app.get("/preview-pdf/{filename:path}", name="preview_pdf")
def preview_pdf(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    pdf_path = _find_preview_pdf_path(safe_name)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF 파일을 찾을 수 없습니다.")

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=safe_name,
        content_disposition_type="inline",
    )


@app.post("/data/upload", response_class=HTMLResponse)
async def upload_master(
    request: Request,
    file: UploadFile = File(...),
    return_page: str = Form("master"),
):
    render = render_master if return_page == "master" else render_home
    try:
        if not file.filename.lower().endswith((".xlsx", ".xlsm")):
            return render(request, error_message="엑셀 파일(.xlsx/.xlsm)만 업로드할 수 있습니다.")
        temp_path = Path(TEMP_DIR) / "_uploaded_master.xlsx"
        with open(temp_path, "wb") as handle:
            handle.write(await file.read())
        data_manager.save_master_excel(str(temp_path), original_filename=file.filename)
        return render(request, success_message="기준 엑셀을 교체했습니다.")
    except Exception as exc:
        return render(request, error_message=f"기준 엑셀 업로드 중 오류가 발생했습니다: {exc}")


@app.post("/inspect", response_class=HTMLResponse)
async def inspect_pdf(
    request: Request,
    region: str = Form(...),
    pdf_files: list[UploadFile] = File(...),
):
    try:
        if region not in REGIONS:
            return render_home(request, error_message="권역 값이 올바르지 않습니다.")
        upload_list = [pdf for pdf in pdf_files if (pdf.filename or "").strip()]
        if not upload_list:
            return render_home(request, error_message="PDF 파일을 한 개 이상 업로드해 주세요.", selected_region=region)

        master_rows = data_manager.load_master_df()
        if not master_rows:
            return render_home(
                request,
                error_message="기준 엑셀 파일이 없습니다. 먼저 기준 엑셀을 업로드해 주세요.",
                selected_region=region,
            )

        inspection_batches = []
        total_parsed = 0
        total_logged_errors = 0
        success_count = 0
        failure_count = 0

        for batch_index, pdf_file in enumerate(upload_list, start=1):
            batch = await _inspect_single_pdf(request, region, pdf_file, master_rows, batch_index)
            inspection_batches.append(batch)
            total_parsed += int(batch.get("parsed_count") or 0)
            total_logged_errors += int(batch.get("logged_errors") or 0)
            if batch.get("error_message"):
                failure_count += 1
            else:
                success_count += 1

        first_preview_batch = next((batch for batch in inspection_batches if batch.get("preview_url")), None)
        first_result_batch = next((batch for batch in inspection_batches if batch.get("results")), None)
        uploaded_pdf_names = [batch.get("display_name") for batch in inspection_batches if batch.get("display_name")]
        uploaded_preview_filename = first_preview_batch.get("preview_filename") if first_preview_batch else None

        success_message = None
        error_message = None
        if success_count == 1 and len(inspection_batches) == 1 and inspection_batches[0].get("summary_message"):
            success_message = inspection_batches[0]["summary_message"]
        elif success_count:
            message_parts = [f"{success_count}개 PDF 검수가 완료되었습니다."]
            if total_parsed:
                message_parts.append(f"총 {total_parsed}개 상품을 추출했습니다.")
            if total_logged_errors:
                message_parts.append(f"오류 로그 {total_logged_errors}건을 저장했습니다.")
            if failure_count:
                message_parts.append(f"{failure_count}개 파일은 파일별 결과에서 오류를 확인해 주세요.")
            success_message = " ".join(message_parts)
        else:
            error_message = "업로드한 PDF를 처리하지 못했습니다. 파일별 오류 내용을 확인해 주세요."

        if success_count:
            inspection_state.replace_last_inspection_pdfs(
                [
                    (batch.get("preview_filename"), Path(batch["preview_path"]))
                    for batch in inspection_batches
                    if batch.get("preview_filename") and batch.get("preview_path")
                ]
            )
            inspection_state.save_last_inspection_state(
                selected_region=region,
                inspection_batches=inspection_batches,
                parsed_count=total_parsed,
                uploaded_pdf_name=first_preview_batch.get("display_name") if first_preview_batch else None,
                uploaded_pdf_names=uploaded_pdf_names,
                uploaded_preview_filename=uploaded_preview_filename,
                success_message=success_message,
            )

        return render_home(
            request,
            selected_region=region,
            results=first_result_batch.get("results") if first_result_batch else None,
            inspection_batches=inspection_batches,
            parsed_count=total_parsed,
            uploaded_pdf_name=first_preview_batch.get("display_name") if first_preview_batch else None,
            uploaded_pdf_names=uploaded_pdf_names,
            uploaded_pdf_url=first_preview_batch.get("preview_url") if first_preview_batch else None,
            success_message=success_message,
            error_message=error_message,
        )
    except RuntimeError as exc:
        return render_home(
            request,
            selected_region=region,
            error_message=str(exc),
        )
    except Exception as exc:
        return render_home(
            request,
            selected_region=region,
            error_message=f"검수 중 오류가 발생했습니다: {exc}",
        )
